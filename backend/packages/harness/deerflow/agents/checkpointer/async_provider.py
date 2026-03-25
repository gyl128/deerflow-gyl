"""Async checkpointer factory.

Provides an **async context manager** for long-running async servers that need
proper resource cleanup.

Supported backends: memory, sqlite, postgres.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

from langgraph.types import Checkpointer

from deerflow.agents.checkpointer.capabilities import (
    AsyncCapabilityAwareSaver,
    log_capability_status,
)
from deerflow.agents.checkpointer.provider import (
    POSTGRES_CONN_REQUIRED,
    POSTGRES_INSTALL,
    SQLITE_INSTALL,
    _resolve_sqlite_conn_str,
)
from deerflow.config.app_config import get_app_config


@contextlib.asynccontextmanager
async def _async_checkpointer(config) -> AsyncIterator[Checkpointer]:
    if config.type == 'memory':
        from langgraph.checkpoint.memory import InMemorySaver

        saver = AsyncCapabilityAwareSaver(InMemorySaver())
        log_capability_status(saver.capability_flags)
        yield saver
        return

    if config.type == 'sqlite':
        try:
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        except ImportError as exc:
            raise ImportError(SQLITE_INSTALL) from exc

        import pathlib

        conn_str = _resolve_sqlite_conn_str(config.connection_string or 'store.db')
        if conn_str != ':memory:' and not conn_str.startswith('file:'):
            await asyncio.to_thread(pathlib.Path(conn_str).parent.mkdir, parents=True, exist_ok=True)
        async with AsyncSqliteSaver.from_conn_string(conn_str) as saver:
            await saver.setup()
            wrapped = AsyncCapabilityAwareSaver(saver)
            log_capability_status(wrapped.capability_flags)
            yield wrapped
        return

    if config.type == 'postgres':
        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        except ImportError as exc:
            raise ImportError(POSTGRES_INSTALL) from exc

        if not config.connection_string:
            raise ValueError(POSTGRES_CONN_REQUIRED)

        async with AsyncPostgresSaver.from_conn_string(config.connection_string) as saver:
            await saver.setup()
            wrapped = AsyncCapabilityAwareSaver(saver)
            log_capability_status(wrapped.capability_flags)
            yield wrapped
        return

    raise ValueError(f'Unknown checkpointer type: {config.type!r}')


@contextlib.asynccontextmanager
async def make_checkpointer() -> AsyncIterator[Checkpointer]:
    config = get_app_config()

    if config.checkpointer is None:
        from langgraph.checkpoint.memory import InMemorySaver

        saver = AsyncCapabilityAwareSaver(InMemorySaver())
        log_capability_status(saver.capability_flags)
        yield saver
        return

    async with _async_checkpointer(config.checkpointer) as saver:
        yield saver
