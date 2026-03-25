from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CapabilityFlags:
    backend: str
    delete_for_runs: bool
    copy_thread: bool
    prune_keep_latest: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            'backend': self.backend,
            'delete_for_runs': self.delete_for_runs,
            'copy_thread': self.copy_thread,
            'prune_keep_latest': self.prune_keep_latest,
        }


def _detect_backend(inner: Any) -> str:
    module = type(inner).__module__
    name = type(inner).__name__
    if module.startswith('langgraph.checkpoint.sqlite') or name in {'SqliteSaver', 'AsyncSqliteSaver'}:
        return 'sqlite'
    if module.startswith('langgraph.checkpoint.memory') or name == 'InMemorySaver':
        return 'memory'
    if 'postgres' in module or 'PostgresSaver' in name:
        return 'postgres'
    return 'custom'


def _build_capability_flags(inner: Any) -> CapabilityFlags:
    backend = _detect_backend(inner)
    if backend in {'sqlite', 'memory'}:
        return CapabilityFlags(
            backend=backend,
            delete_for_runs=True,
            copy_thread=True,
            prune_keep_latest=True,
        )
    return CapabilityFlags(
        backend=backend,
        delete_for_runs=False,
        copy_thread=True,
        prune_keep_latest=False,
    )


def log_capability_status(flags: CapabilityFlags) -> None:
    status = json.dumps(flags.as_dict(), sort_keys=True)
    if flags.delete_for_runs and flags.prune_keep_latest:
        logger.info('Checkpointer capability status: %s', status)
        return
    logger.warning('Checkpointer capability status degraded: %s', status)


async def _async_copy_thread(inner: BaseCheckpointSaver, source_thread_id: str, target_thread_id: str) -> None:
    checkpoints = [item async for item in inner.alist({'configurable': {'thread_id': source_thread_id}})]
    checkpoints.sort(key=lambda item: item.config['configurable']['checkpoint_id'])
    for checkpoint in checkpoints:
        checkpoint_ns = checkpoint.config['configurable'].get('checkpoint_ns', '')
        new_config: dict[str, Any] = {
            'configurable': {
                'thread_id': target_thread_id,
                'checkpoint_ns': checkpoint_ns,
            }
        }
        if checkpoint.parent_config and checkpoint.parent_config.get('configurable'):
            parent_id = checkpoint.parent_config['configurable'].get('checkpoint_id')
            if parent_id is not None:
                new_config['configurable']['checkpoint_id'] = parent_id
        metadata = dict(checkpoint.metadata)
        if 'thread_id' in metadata:
            metadata['thread_id'] = target_thread_id
        stored_config = await inner.aput(
            new_config,
            checkpoint.checkpoint,
            metadata,
            checkpoint.checkpoint.get('channel_versions', {}),
        )
        if checkpoint.pending_writes:
            writes_by_task: dict[str, list[tuple[str, Any]]] = {}
            for task_id, channel, value in checkpoint.pending_writes:
                writes_by_task.setdefault(task_id, []).append((channel, value))
            for task_id, writes in writes_by_task.items():
                await inner.aput_writes(stored_config, writes, task_id)


def _sync_copy_thread(inner: BaseCheckpointSaver, source_thread_id: str, target_thread_id: str) -> None:
    checkpoints = list(inner.list({'configurable': {'thread_id': source_thread_id}}))
    checkpoints.sort(key=lambda item: item.config['configurable']['checkpoint_id'])
    for checkpoint in checkpoints:
        checkpoint_ns = checkpoint.config['configurable'].get('checkpoint_ns', '')
        new_config: dict[str, Any] = {
            'configurable': {
                'thread_id': target_thread_id,
                'checkpoint_ns': checkpoint_ns,
            }
        }
        if checkpoint.parent_config and checkpoint.parent_config.get('configurable'):
            parent_id = checkpoint.parent_config['configurable'].get('checkpoint_id')
            if parent_id is not None:
                new_config['configurable']['checkpoint_id'] = parent_id
        metadata = dict(checkpoint.metadata)
        if 'thread_id' in metadata:
            metadata['thread_id'] = target_thread_id
        stored_config = inner.put(
            new_config,
            checkpoint.checkpoint,
            metadata,
            checkpoint.checkpoint.get('channel_versions', {}),
        )
        if checkpoint.pending_writes:
            writes_by_task: dict[str, list[tuple[str, Any]]] = {}
            for task_id, channel, value in checkpoint.pending_writes:
                writes_by_task.setdefault(task_id, []).append((channel, value))
            for task_id, writes in writes_by_task.items():
                inner.put_writes(stored_config, writes, task_id)


class AsyncCapabilityAwareSaver(BaseCheckpointSaver):
    def __init__(self, inner: BaseCheckpointSaver) -> None:
        self._inner = inner
        self.capability_flags = _build_capability_flags(inner)
        super().__init__(serde=getattr(inner, 'serde', None))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def aget(self, config: RunnableConfig) -> Checkpoint | None:
        return await self._inner.aget(config)

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        return await self._inner.aget_tuple(config)

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        return await self._inner.aput(config, checkpoint, metadata, new_versions)

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = '',
    ) -> None:
        await self._inner.aput_writes(config, writes, task_id, task_path)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        async for item in self._inner.alist(config, filter=filter, before=before, limit=limit):
            yield item

    async def adelete_thread(self, thread_id: str) -> None:
        await self._inner.adelete_thread(thread_id)

    async def adelete_for_runs(self, run_ids: Iterable[str]) -> None:
        run_ids = [str(run_id) for run_id in run_ids if str(run_id)]
        if not run_ids:
            return
        if self.capability_flags.backend == 'sqlite':
            await self._sqlite_delete_for_runs(run_ids)
            return
        if self.capability_flags.backend == 'memory':
            await self._memory_delete_for_runs(run_ids)
            return
        raise RuntimeError(
            f"Checkpointer backend '{self.capability_flags.backend}' does not support adelete_for_runs. "
            f"capabilities={self.capability_flags.as_dict()}"
        )

    async def acopy_thread(self, source_thread_id: str, target_thread_id: str) -> None:
        await _async_copy_thread(self._inner, source_thread_id, target_thread_id)

    async def aprune(self, thread_ids: Sequence[str], *, strategy: str = 'keep_latest') -> None:
        if strategy == 'delete_all':
            for thread_id in thread_ids:
                await self.adelete_thread(str(thread_id))
            return
        if strategy != 'keep_latest':
            raise ValueError(f'Unsupported prune strategy: {strategy}')
        if self.capability_flags.backend == 'sqlite':
            await self._sqlite_prune_keep_latest([str(thread_id) for thread_id in thread_ids])
            return
        if self.capability_flags.backend == 'memory':
            await self._memory_prune_keep_latest([str(thread_id) for thread_id in thread_ids])
            return
        raise RuntimeError(
            f"Checkpointer backend '{self.capability_flags.backend}' does not support aprune(keep_latest). "
            f"capabilities={self.capability_flags.as_dict()}"
        )

    def get_next_version(self, current: str | None, channel: Any) -> str:
        return self._inner.get_next_version(current, channel)

    async def _sqlite_delete_for_runs(self, run_ids: Sequence[str]) -> None:
        await self._inner.setup()
        placeholders = ', '.join('?' for _ in run_ids)
        query = (
            'SELECT thread_id, checkpoint_ns, checkpoint_id FROM checkpoints '
            f"WHERE json_extract(CAST(metadata AS TEXT), '$.run_id') IN ({placeholders})"
        )
        async with self._inner.lock, self._inner.conn.cursor() as cur:
            await cur.execute(query, tuple(run_ids))
            rows = await cur.fetchall()
            if not rows:
                return
            await cur.executemany(
                'DELETE FROM writes WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?',
                rows,
            )
            await cur.executemany(
                'DELETE FROM checkpoints WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?',
                rows,
            )
            await cur.executemany(
                'UPDATE checkpoints SET parent_checkpoint_id = NULL WHERE thread_id = ? AND checkpoint_ns = ? AND parent_checkpoint_id = ?',
                rows,
            )
            await self._inner.conn.commit()

    async def _sqlite_prune_keep_latest(self, thread_ids: Sequence[str]) -> None:
        if not thread_ids:
            return
        await self._inner.setup()
        async with self._inner.lock, self._inner.conn.cursor() as cur:
            for thread_id in thread_ids:
                await cur.execute(
                    'SELECT checkpoint_ns, checkpoint_id FROM checkpoints WHERE thread_id = ? ORDER BY checkpoint_ns ASC, checkpoint_id DESC',
                    (thread_id,),
                )
                rows = await cur.fetchall()
                keep_latest: dict[str, str] = {}
                stale_rows: list[tuple[str, str, str]] = []
                for checkpoint_ns, checkpoint_id in rows:
                    if checkpoint_ns not in keep_latest:
                        keep_latest[checkpoint_ns] = checkpoint_id
                    else:
                        stale_rows.append((thread_id, checkpoint_ns, checkpoint_id))
                if stale_rows:
                    await cur.executemany(
                        'DELETE FROM writes WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?',
                        stale_rows,
                    )
                    await cur.executemany(
                        'DELETE FROM checkpoints WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?',
                        stale_rows,
                    )
                for checkpoint_ns, checkpoint_id in keep_latest.items():
                    await cur.execute(
                        'UPDATE checkpoints SET parent_checkpoint_id = NULL WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?',
                        (thread_id, checkpoint_ns, checkpoint_id),
                    )
            await self._inner.conn.commit()

    async def _memory_delete_for_runs(self, run_ids: Sequence[str]) -> None:
        storage = getattr(self._inner, 'storage')
        writes = getattr(self._inner, 'writes')
        run_ids_set = set(run_ids)
        for thread_id, namespaces in list(storage.items()):
            for checkpoint_ns, checkpoints in list(namespaces.items()):
                to_delete: list[str] = []
                for checkpoint_id, (_, metadata_bytes, _) in list(checkpoints.items()):
                    metadata = cast(dict[str, Any], self._inner.serde.loads_typed(metadata_bytes))
                    if str(metadata.get('run_id', '')) in run_ids_set:
                        to_delete.append(checkpoint_id)
                for checkpoint_id in to_delete:
                    checkpoints.pop(checkpoint_id, None)
                    writes.pop((thread_id, checkpoint_ns, checkpoint_id), None)
                if not checkpoints:
                    namespaces.pop(checkpoint_ns, None)
            if not namespaces:
                storage.pop(thread_id, None)

    async def _memory_prune_keep_latest(self, thread_ids: Sequence[str]) -> None:
        storage = getattr(self._inner, 'storage')
        writes = getattr(self._inner, 'writes')
        for thread_id in thread_ids:
            namespaces = storage.get(thread_id, {})
            for checkpoint_ns, checkpoints in list(namespaces.items()):
                ordered_ids = sorted(checkpoints.keys(), reverse=True)
                keep_id = ordered_ids[0] if ordered_ids else None
                for checkpoint_id in ordered_ids[1:]:
                    checkpoints.pop(checkpoint_id, None)
                    writes.pop((thread_id, checkpoint_ns, checkpoint_id), None)
                if keep_id is not None:
                    checkpoint, metadata, _parent = checkpoints[keep_id]
                    checkpoints[keep_id] = (checkpoint, metadata, None)


class SyncCapabilityAwareSaver(BaseCheckpointSaver):
    def __init__(self, inner: BaseCheckpointSaver) -> None:
        self._inner = inner
        self.capability_flags = _build_capability_flags(inner)
        super().__init__(serde=getattr(inner, 'serde', None))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def get(self, config: RunnableConfig) -> Checkpoint | None:
        return self._inner.get(config)

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        return self._inner.get_tuple(config)

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        return self._inner.put(config, checkpoint, metadata, new_versions)

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = '',
    ) -> None:
        self._inner.put_writes(config, writes, task_id, task_path)

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        yield from self._inner.list(config, filter=filter, before=before, limit=limit)

    def delete_thread(self, thread_id: str) -> None:
        self._inner.delete_thread(thread_id)

    def delete_for_runs(self, run_ids: Iterable[str]) -> None:
        run_ids = [str(run_id) for run_id in run_ids if str(run_id)]
        if not run_ids:
            return
        if self.capability_flags.backend == 'sqlite':
            self._sqlite_delete_for_runs(run_ids)
            return
        if self.capability_flags.backend == 'memory':
            self._memory_delete_for_runs(run_ids)
            return
        raise RuntimeError(
            f"Checkpointer backend '{self.capability_flags.backend}' does not support delete_for_runs. "
            f"capabilities={self.capability_flags.as_dict()}"
        )

    def copy_thread(self, source_thread_id: str, target_thread_id: str) -> None:
        _sync_copy_thread(self._inner, source_thread_id, target_thread_id)

    def prune(self, thread_ids: Sequence[str], *, strategy: str = 'keep_latest') -> None:
        if strategy == 'delete_all':
            for thread_id in thread_ids:
                self.delete_thread(str(thread_id))
            return
        if strategy != 'keep_latest':
            raise ValueError(f'Unsupported prune strategy: {strategy}')
        if self.capability_flags.backend == 'sqlite':
            self._sqlite_prune_keep_latest([str(thread_id) for thread_id in thread_ids])
            return
        if self.capability_flags.backend == 'memory':
            self._memory_prune_keep_latest([str(thread_id) for thread_id in thread_ids])
            return
        raise RuntimeError(
            f"Checkpointer backend '{self.capability_flags.backend}' does not support prune(keep_latest). "
            f"capabilities={self.capability_flags.as_dict()}"
        )

    def get_next_version(self, current: str | None, channel: Any) -> str:
        return self._inner.get_next_version(current, channel)

    def _sqlite_delete_for_runs(self, run_ids: Sequence[str]) -> None:
        self._inner.setup()
        placeholders = ', '.join('?' for _ in run_ids)
        query = (
            'SELECT thread_id, checkpoint_ns, checkpoint_id FROM checkpoints '
            f"WHERE json_extract(CAST(metadata AS TEXT), '$.run_id') IN ({placeholders})"
        )
        with self._inner.lock, self._inner.conn:
            rows = list(self._inner.conn.execute(query, tuple(run_ids)))
            if not rows:
                return
            self._inner.conn.executemany(
                'DELETE FROM writes WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?',
                rows,
            )
            self._inner.conn.executemany(
                'DELETE FROM checkpoints WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?',
                rows,
            )
            self._inner.conn.executemany(
                'UPDATE checkpoints SET parent_checkpoint_id = NULL WHERE thread_id = ? AND checkpoint_ns = ? AND parent_checkpoint_id = ?',
                rows,
            )

    def _sqlite_prune_keep_latest(self, thread_ids: Sequence[str]) -> None:
        if not thread_ids:
            return
        self._inner.setup()
        with self._inner.lock, self._inner.conn:
            for thread_id in thread_ids:
                rows = list(
                    self._inner.conn.execute(
                        'SELECT checkpoint_ns, checkpoint_id FROM checkpoints WHERE thread_id = ? ORDER BY checkpoint_ns ASC, checkpoint_id DESC',
                        (thread_id,),
                    )
                )
                keep_latest: dict[str, str] = {}
                stale_rows: list[tuple[str, str, str]] = []
                for checkpoint_ns, checkpoint_id in rows:
                    if checkpoint_ns not in keep_latest:
                        keep_latest[checkpoint_ns] = checkpoint_id
                    else:
                        stale_rows.append((thread_id, checkpoint_ns, checkpoint_id))
                if stale_rows:
                    self._inner.conn.executemany(
                        'DELETE FROM writes WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?',
                        stale_rows,
                    )
                    self._inner.conn.executemany(
                        'DELETE FROM checkpoints WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?',
                        stale_rows,
                    )
                self._inner.conn.executemany(
                    'UPDATE checkpoints SET parent_checkpoint_id = NULL WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?',
                    [(thread_id, checkpoint_ns, checkpoint_id) for checkpoint_ns, checkpoint_id in keep_latest.items()],
                )

    def _memory_delete_for_runs(self, run_ids: Sequence[str]) -> None:
        storage = getattr(self._inner, 'storage')
        writes = getattr(self._inner, 'writes')
        run_ids_set = set(run_ids)
        for thread_id, namespaces in list(storage.items()):
            for checkpoint_ns, checkpoints in list(namespaces.items()):
                to_delete: list[str] = []
                for checkpoint_id, (_, metadata_bytes, _) in list(checkpoints.items()):
                    metadata = cast(dict[str, Any], self._inner.serde.loads_typed(metadata_bytes))
                    if str(metadata.get('run_id', '')) in run_ids_set:
                        to_delete.append(checkpoint_id)
                for checkpoint_id in to_delete:
                    checkpoints.pop(checkpoint_id, None)
                    writes.pop((thread_id, checkpoint_ns, checkpoint_id), None)
                if not checkpoints:
                    namespaces.pop(checkpoint_ns, None)
            if not namespaces:
                storage.pop(thread_id, None)

    def _memory_prune_keep_latest(self, thread_ids: Sequence[str]) -> None:
        storage = getattr(self._inner, 'storage')
        writes = getattr(self._inner, 'writes')
        for thread_id in thread_ids:
            namespaces = storage.get(thread_id, {})
            for checkpoint_ns, checkpoints in list(namespaces.items()):
                ordered_ids = sorted(checkpoints.keys(), reverse=True)
                keep_id = ordered_ids[0] if ordered_ids else None
                for checkpoint_id in ordered_ids[1:]:
                    checkpoints.pop(checkpoint_id, None)
                    writes.pop((thread_id, checkpoint_ns, checkpoint_id), None)
                if keep_id is not None:
                    checkpoint, metadata, _parent = checkpoints[keep_id]
                    checkpoints[keep_id] = (checkpoint, metadata, None)
