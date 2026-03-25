"""ChannelService - manages the lifecycle of all IM channels."""

from __future__ import annotations

import logging
from typing import Any

from app.channels.manager import ChannelManager
from app.channels.message_bus import MessageBus
from app.channels.store import ChannelStore

logger = logging.getLogger(__name__)

_CHANNEL_REGISTRY: dict[str, str] = {
    'feishu': 'app.channels.feishu:FeishuChannel',
    'slack': 'app.channels.slack:SlackChannel',
    'telegram': 'app.channels.telegram:TelegramChannel',
}


class NoChannelsConfiguredError(RuntimeError):
    """Raised when no IM channels are enabled in config."""


class ChannelService:
    def __init__(self, channels_config: dict[str, Any] | None = None) -> None:
        self.bus = MessageBus()
        self.store = ChannelStore()
        config = dict(channels_config or {})
        langgraph_url = config.pop('langgraph_url', None) or 'http://localhost:2024'
        gateway_url = config.pop('gateway_url', None) or 'http://localhost:8001'
        default_session = config.pop('session', None)
        channel_sessions = {
            name: channel_config.get('session')
            for name, channel_config in config.items()
            if isinstance(channel_config, dict)
        }
        self.manager = ChannelManager(
            bus=self.bus,
            store=self.store,
            langgraph_url=langgraph_url,
            gateway_url=gateway_url,
            default_session=default_session if isinstance(default_session, dict) else None,
            channel_sessions=channel_sessions,
        )
        self._channels: dict[str, Any] = {}
        self._config = config
        self._running = False
        self._startup_errors: dict[str, str] = {}

    @classmethod
    def from_app_config(cls) -> ChannelService:
        from deerflow.config.app_config import get_app_config

        config = get_app_config()
        channels_config = {}
        extra = config.model_extra or {}
        if 'channels' in extra:
            channels_config = extra['channels']
        return cls(channels_config=channels_config)

    @property
    def enabled_channel_names(self) -> list[str]:
        return [
            name
            for name, channel_config in self._config.items()
            if isinstance(channel_config, dict) and channel_config.get('enabled', False)
        ]

    @property
    def has_enabled_channels(self) -> bool:
        return bool(self.enabled_channel_names)

    @property
    def has_failed_channels(self) -> bool:
        return bool(self._startup_errors)

    async def start(self) -> None:
        if self._running:
            return

        if not self.has_enabled_channels:
            raise NoChannelsConfiguredError('No IM channels are enabled in config.')

        self._startup_errors = {}
        await self.manager.start()

        for name, channel_config in self._config.items():
            if not isinstance(channel_config, dict):
                continue
            if not channel_config.get('enabled', False):
                logger.info('Channel %s is disabled, skipping', name)
                continue
            await self._start_channel(name, channel_config)

        self._running = True
        logger.info('ChannelService started with channels: %s', list(self._channels.keys()))

    async def stop(self) -> None:
        for name, channel in list(self._channels.items()):
            try:
                await channel.stop()
                logger.info('Channel %s stopped', name)
            except Exception:
                logger.exception('Error stopping channel %s', name)
        self._channels.clear()

        await self.manager.stop()
        self._running = False
        logger.info('ChannelService stopped')

    async def restart_channel(self, name: str) -> bool:
        if name in self._channels:
            try:
                await self._channels[name].stop()
            except Exception:
                logger.exception('Error stopping channel %s for restart', name)
            del self._channels[name]

        config = self._config.get(name)
        if not config or not isinstance(config, dict):
            logger.warning('No config for channel %s', name)
            return False

        return await self._start_channel(name, config)

    async def _start_channel(self, name: str, config: dict[str, Any]) -> bool:
        import_path = _CHANNEL_REGISTRY.get(name)
        if not import_path:
            message = f'Unknown channel type: {name}'
            logger.warning(message)
            self._startup_errors[name] = message
            return False

        try:
            from deerflow.reflection import resolve_class

            channel_cls = resolve_class(import_path, base_class=None)
        except Exception as exc:
            logger.exception('Failed to import channel class for %s', name)
            self._startup_errors[name] = f'import failed: {exc}'
            return False

        try:
            channel = channel_cls(bus=self.bus, config=config)
            await channel.start()
            self._channels[name] = channel
            self._startup_errors.pop(name, None)
            logger.info('Channel %s started', name)
            return True
        except Exception as exc:
            logger.exception('Failed to start channel %s', name)
            self._startup_errors[name] = f'start failed: {exc}'
            return False

    def get_status(self) -> dict[str, Any]:
        channels_status = {}
        for name in _CHANNEL_REGISTRY:
            config = self._config.get(name, {})
            enabled = isinstance(config, dict) and config.get('enabled', False)
            running = name in self._channels and self._channels[name].is_running
            channels_status[name] = {
                'enabled': enabled,
                'running': running,
                'error': self._startup_errors.get(name),
            }
        if not self.has_enabled_channels:
            overall = 'disabled'
            reason = 'no IM channels configured'
        elif self.has_failed_channels:
            overall = 'degraded'
            reason = 'one or more enabled channels failed to start'
        else:
            overall = 'healthy'
            reason = None
        return {
            'status': overall,
            'reason': reason,
            'service_running': self._running,
            'enabled_channels': self.enabled_channel_names,
            'running_channels': sorted(self._channels.keys()),
            'failed_channels': dict(self._startup_errors),
            'channels': channels_status,
        }


_channel_service: ChannelService | None = None


def get_channel_service() -> ChannelService | None:
    return _channel_service


async def start_channel_service() -> ChannelService:
    global _channel_service
    if _channel_service is not None:
        return _channel_service
    service = ChannelService.from_app_config()
    await service.start()
    _channel_service = service
    return _channel_service


async def stop_channel_service() -> None:
    global _channel_service
    if _channel_service is not None:
        await _channel_service.stop()
        _channel_service = None
