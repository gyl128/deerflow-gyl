"""Gateway router for IM channel management."""

from __future__ import annotations

import logging
import os

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/channels", tags=["channels"])


class ChannelStatusResponse(BaseModel):
    status: str = 'disabled'
    reason: str | None = None
    service_running: bool
    enabled_channels: list[str] = []
    running_channels: list[str] = []
    failed_channels: dict[str, str] = {}
    channels: dict[str, dict]


class ChannelRestartResponse(BaseModel):
    success: bool
    message: str


def _channel_service_mode() -> str:
    return os.getenv('DEER_FLOW_CHANNEL_SERVICE_MODE', 'embedded')


def _external_channel_health_url() -> str:
    return os.getenv('DEER_FLOW_CHANNEL_WORKER_HEALTH_URL', 'http://channel-worker:8010/health')


@router.get("", response_model=ChannelStatusResponse)
@router.get("/", response_model=ChannelStatusResponse)
async def get_channels_status() -> ChannelStatusResponse:
    """Get the status of all IM channels."""
    if _channel_service_mode() == 'external':
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.get(_external_channel_health_url())
            response.raise_for_status()
            payload = response.json()
            channel_status = payload.get('channels')
            if not isinstance(channel_status, dict):
                raise ValueError('channel-worker health payload missing channels object')
            return ChannelStatusResponse(**channel_status)
        except Exception as exc:
            logger.exception("Failed to query external channel worker health: %s", exc)
            return ChannelStatusResponse(
                status='degraded',
                reason=f'external channel worker probe failed: {exc}',
                service_running=False,
                channels={},
            )

    from app.channels.service import get_channel_service

    service = get_channel_service()
    if service is None:
        return ChannelStatusResponse(service_running=False, channels={})
    status = service.get_status()
    return ChannelStatusResponse(**status)


@router.post("/{name}/restart", response_model=ChannelRestartResponse)
async def restart_channel(name: str) -> ChannelRestartResponse:
    """Restart a specific IM channel."""
    from app.channels.service import get_channel_service

    service = get_channel_service()
    if service is None:
        raise HTTPException(status_code=503, detail="Channel service is not running")

    success = await service.restart_channel(name)
    if success:
        logger.info("Channel %s restarted successfully", name)
        return ChannelRestartResponse(success=True, message=f"Channel {name} restarted successfully")
    else:
        logger.warning("Failed to restart channel %s", name)
        return ChannelRestartResponse(success=False, message=f"Failed to restart channel {name}")
