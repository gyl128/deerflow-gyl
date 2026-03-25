from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.channels.service import ChannelService, NoChannelsConfiguredError

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)

logger = logging.getLogger(__name__)


def _selected_channels_from_env() -> list[str]:
    raw = os.getenv('DEER_FLOW_CHANNEL_NAMES', '')
    return [name.strip() for name in raw.split(',') if name.strip()]


def _default_status() -> dict[str, Any]:
    return {
        'status': 'disabled',
        'reason': 'channel worker not started',
        'service_running': False,
        'enabled_channels': [],
        'running_channels': [],
        'failed_channels': {},
        'channels': {},
    }


async def _start_selected_channel_service(channel_names: Sequence[str]) -> ChannelService:
    service = ChannelService.from_app_config(selected_channels=channel_names)
    await service.start()
    return service


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    selected_channels = _selected_channels_from_env()
    app.state.channel_status = _default_status()
    app.state.channel_service = None
    app.state.channel_worker = {
        'mode': 'external',
        'selected_channels': selected_channels,
    }

    try:
        service = await _start_selected_channel_service(selected_channels)
        app.state.channel_service = service
        app.state.channel_status = service.get_status()
        logger.info('External channel worker started: %s', app.state.channel_status)
    except NoChannelsConfiguredError as exc:
        app.state.channel_status = {
            **_default_status(),
            'status': 'disabled',
            'reason': str(exc),
        }
        logger.info('External channel worker disabled: %s', exc)
    except Exception as exc:
        app.state.channel_status = {
            **_default_status(),
            'status': 'degraded',
            'reason': str(exc),
        }
        logger.exception('External channel worker failed to start: %s', exc)

    yield

    service = app.state.channel_service
    if service is not None:
        await service.stop()


def create_app() -> FastAPI:
    app = FastAPI(
        title='DeerFlow Channel Worker',
        version='0.1.0',
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.channel_status = _default_status()

    @app.get('/health')
    async def health() -> JSONResponse:
        payload = {
            'status': app.state.channel_status['status'],
            'service': 'deer-flow-channel-worker',
            'worker': app.state.channel_worker,
            'channels': app.state.channel_status,
        }
        http_status = 200 if app.state.channel_status['status'] != 'degraded' else 503
        return JSONResponse(status_code=http_status, content=payload)

    return app


app = create_app()
