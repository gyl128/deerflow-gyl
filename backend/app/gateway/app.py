import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.gateway.config import get_gateway_config
from app.gateway.routers import (
    agents,
    artifacts,
    channels,
    mcp,
    memory,
    models,
    skills,
    suggestions,
    uploads,
)
from deerflow.config.app_config import get_app_config

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)

logger = logging.getLogger(__name__)


def _default_channel_health() -> dict[str, Any]:
    return {
        'status': 'disabled',
        'reason': 'channel service not started',
        'service_running': False,
        'enabled_channels': [],
        'running_channels': [],
        'failed_channels': {},
        'channels': {},
    }


def _runtime_metadata() -> dict[str, Any]:
    config = get_app_config()
    checkpointer = getattr(config, 'checkpointer', None)
    return {
        'runtime_mode': os.getenv('DEER_FLOW_RUNTIME_MODE', 'unknown'),
        'config_path': os.getenv('DEER_FLOW_CONFIG_PATH') or 'config.yaml',
        'langgraph_ready_url': os.getenv('DEER_FLOW_LANGGRAPH_READY_URL', 'http://127.0.0.1:2024/docs'),
        'checkpointer': {
            'type': getattr(checkpointer, 'type', None),
            'configured': checkpointer is not None,
        },
    }


async def _probe_langgraph() -> tuple[bool, str | None]:
    url = os.getenv('DEER_FLOW_LANGGRAPH_READY_URL', 'http://127.0.0.1:2024/docs')
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(url)
        if response.is_success:
            return True, None
        return False, f'langgraph probe returned {response.status_code}'
    except Exception as exc:
        return False, f'langgraph probe failed: {exc}'


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    try:
        config = get_app_config()
        logger.info('Configuration loaded successfully')
        logger.info(
            'Gateway runtime metadata: %s',
            {
                **_runtime_metadata(),
                'channels_defined': sorted((config.model_extra or {}).get('channels', {}).keys()),
            },
        )
    except Exception as e:
        error_msg = f'Failed to load configuration during gateway startup: {e}'
        logger.exception(error_msg)
        raise RuntimeError(error_msg) from e
    gateway_config = get_gateway_config()
    logger.info('Starting API Gateway on %s:%s', gateway_config.host, gateway_config.port)
    app.state.channel_health = _default_channel_health()

    try:
        from app.channels.service import NoChannelsConfiguredError, start_channel_service

        channel_service = await start_channel_service()
        channel_status = channel_service.get_status()
        app.state.channel_health = channel_status
        if channel_status['status'] == 'degraded':
            logger.error('Channel service started in degraded state: %s', channel_status)
        else:
            logger.info('Channel service started: %s', channel_status)
    except NoChannelsConfiguredError as exc:
        app.state.channel_health = {
            **_default_channel_health(),
            'status': 'disabled',
            'reason': str(exc),
        }
        logger.info('No IM channels configured: %s', exc)
    except Exception as exc:
        app.state.channel_health = {
            **_default_channel_health(),
            'status': 'degraded',
            'reason': str(exc),
        }
        logger.exception('Channel service failed to start: %s', exc)

    yield

    try:
        from app.channels.service import stop_channel_service

        await stop_channel_service()
    except Exception:
        logger.exception('Failed to stop channel service')
    logger.info('Shutting down API Gateway')


def create_app() -> FastAPI:
    app = FastAPI(
        title='DeerFlow API Gateway',
        description='''
## DeerFlow API Gateway

API Gateway for DeerFlow - A LangGraph-based AI agent backend with sandbox execution capabilities.
        ''',
        version='0.1.0',
        lifespan=lifespan,
        docs_url='/docs',
        redoc_url='/redoc',
        openapi_url='/openapi.json',
        openapi_tags=[
            {'name': 'models', 'description': 'Operations for querying available AI models and their configurations'},
            {'name': 'mcp', 'description': 'Manage Model Context Protocol (MCP) server configurations'},
            {'name': 'memory', 'description': 'Access and manage global memory data for personalized conversations'},
            {'name': 'skills', 'description': 'Manage skills and their configurations'},
            {'name': 'artifacts', 'description': 'Access and download thread artifacts and generated files'},
            {'name': 'uploads', 'description': 'Upload and manage user files for threads'},
            {'name': 'agents', 'description': 'Create and manage custom agents with per-agent config and prompts'},
            {'name': 'suggestions', 'description': 'Generate follow-up question suggestions for conversations'},
            {'name': 'channels', 'description': 'Manage IM channel integrations (Feishu, Slack, Telegram)'},
            {'name': 'health', 'description': 'Health check and system status endpoints'},
        ],
    )
    app.state.channel_health = _default_channel_health()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            'http://localhost:3001',
            'http://127.0.0.1:3001',
            'http://172.20.65.130:3001',
            '*',
        ],
        allow_credentials=True,
        allow_methods=['*'],
        allow_headers=['*'],
    )

    app.include_router(models.router)
    app.include_router(mcp.router)
    app.include_router(memory.router)
    app.include_router(skills.router)
    app.include_router(artifacts.router)
    app.include_router(uploads.router)
    app.include_router(agents.router)
    app.include_router(suggestions.router)
    app.include_router(channels.router)

    @app.get('/health', tags=['health'])
    async def health_check() -> dict[str, Any]:
        return {
            'status': 'healthy',
            'service': 'deer-flow-gateway',
            'channels': app.state.channel_health,
            'runtime': _runtime_metadata(),
        }

    @app.get('/ready', tags=['health'])
    async def ready_check() -> JSONResponse:
        runtime = _runtime_metadata()
        issues: list[str] = []
        if not runtime['checkpointer']['configured']:
            issues.append('checkpointer is not configured')
        langgraph_ok, langgraph_error = await _probe_langgraph()
        if not langgraph_ok and langgraph_error:
            issues.append(langgraph_error)
        payload = {
            'status': 'ready' if not issues else 'not_ready',
            'service': 'deer-flow-gateway',
            'channels': app.state.channel_health,
            'runtime': runtime,
            'checks': {
                'langgraph': {'ok': langgraph_ok, 'error': langgraph_error},
                'checkpointer': {
                    'ok': runtime['checkpointer']['configured'],
                    'error': None if runtime['checkpointer']['configured'] else 'checkpointer missing',
                },
            },
        }
        return JSONResponse(status_code=200 if not issues else 503, content=payload)

    return app


app = create_app()
