"""Sync checkpointer factory."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterator

from langgraph.types import Checkpointer

from deerflow.agents.checkpointer.capabilities import (
    SyncCapabilityAwareSaver,
    log_capability_status,
)
from deerflow.config.app_config import get_app_config
from deerflow.config.checkpointer_config import CheckpointerConfig
from deerflow.config.paths import resolve_path

logger = logging.getLogger(__name__)

SQLITE_INSTALL = 'langgraph-checkpoint-sqlite is required for the SQLite checkpointer. Install it with: uv add langgraph-checkpoint-sqlite'
POSTGRES_INSTALL = 'langgraph-checkpoint-postgres is required for the PostgreSQL checkpointer. Install it with: uv add langgraph-checkpoint-postgres psycopg[binary] psycopg-pool'
POSTGRES_CONN_REQUIRED = 'checkpointer.connection_string is required for the postgres backend'


def _resolve_sqlite_conn_str(raw: str) -> str:
    if raw == ':memory:' or raw.startswith('file:'):
        return raw
    return str(resolve_path(raw))


@contextlib.contextmanager
def _sync_checkpointer_cm(config: CheckpointerConfig) -> Iterator[Checkpointer]:
    if config.type == 'memory':
        from langgraph.checkpoint.memory import InMemorySaver

        saver = SyncCapabilityAwareSaver(InMemorySaver())
        log_capability_status(saver.capability_flags)
        logger.info('Checkpointer: using InMemorySaver (in-process, not persistent)')
        yield saver
        return

    if config.type == 'sqlite':
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver
        except ImportError as exc:
            raise ImportError(SQLITE_INSTALL) from exc

        conn_str = _resolve_sqlite_conn_str(config.connection_string or 'store.db')
        with SqliteSaver.from_conn_string(conn_str) as saver:
            saver.setup()
            wrapped = SyncCapabilityAwareSaver(saver)
            log_capability_status(wrapped.capability_flags)
            logger.info('Checkpointer: using SqliteSaver (%s)', conn_str)
            yield wrapped
        return

    if config.type == 'postgres':
        try:
            from langgraph.checkpoint.postgres import PostgresSaver
        except ImportError as exc:
            raise ImportError(POSTGRES_INSTALL) from exc

        if not config.connection_string:
            raise ValueError(POSTGRES_CONN_REQUIRED)

        with PostgresSaver.from_conn_string(config.connection_string) as saver:
            saver.setup()
            wrapped = SyncCapabilityAwareSaver(saver)
            log_capability_status(wrapped.capability_flags)
            logger.info('Checkpointer: using PostgresSaver')
            yield wrapped
        return

    raise ValueError(f'Unknown checkpointer type: {config.type!r}')


_checkpointer: Checkpointer | None = None
_checkpointer_ctx = None


def get_checkpointer() -> Checkpointer:
    global _checkpointer, _checkpointer_ctx

    if _checkpointer is not None:
        return _checkpointer

    from deerflow.config.app_config import _app_config
    from deerflow.config.checkpointer_config import get_checkpointer_config

    config = get_checkpointer_config()

    if config is None and _app_config is None:
        try:
            get_app_config()
        except FileNotFoundError:
            pass
        config = get_checkpointer_config()
    if config is None:
        from langgraph.checkpoint.memory import InMemorySaver

        saver = SyncCapabilityAwareSaver(InMemorySaver())
        log_capability_status(saver.capability_flags)
        logger.info('Checkpointer: using InMemorySaver (in-process, not persistent)')
        _checkpointer = saver
        return _checkpointer

    _checkpointer_ctx = _sync_checkpointer_cm(config)
    _checkpointer = _checkpointer_ctx.__enter__()

    return _checkpointer


def reset_checkpointer() -> None:
    global _checkpointer, _checkpointer_ctx
    if _checkpointer_ctx is not None:
        try:
            _checkpointer_ctx.__exit__(None, None, None)
        except Exception:
            logger.warning('Error during checkpointer cleanup', exc_info=True)
        _checkpointer_ctx = None
    _checkpointer = None


@contextlib.contextmanager
def checkpointer_context() -> Iterator[Checkpointer]:
    config = get_app_config()
    if config.checkpointer is None:
        from langgraph.checkpoint.memory import InMemorySaver

        saver = SyncCapabilityAwareSaver(InMemorySaver())
        log_capability_status(saver.capability_flags)
        yield saver
        return

    with _sync_checkpointer_cm(config.checkpointer) as saver:
        yield saver
