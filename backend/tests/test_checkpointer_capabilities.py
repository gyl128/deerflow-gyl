from pathlib import Path

from langgraph.checkpoint.base import empty_checkpoint

from deerflow.agents.checkpointer import get_checkpointer, reset_checkpointer
from deerflow.config.checkpointer_config import load_checkpointer_config_from_dict, set_checkpointer_config


def _checkpoint(checkpoint_id: str):
    checkpoint = empty_checkpoint()
    checkpoint['id'] = checkpoint_id
    return checkpoint


def test_sqlite_delete_for_runs_removes_rolled_back_checkpoint(tmp_path):
    db_path = tmp_path / 'delete-for-runs.db'
    set_checkpointer_config(None)
    reset_checkpointer()
    load_checkpointer_config_from_dict({'type': 'sqlite', 'connection_string': str(db_path)})

    saver = get_checkpointer()
    first_config = saver.put({'configurable': {'thread_id': 'thread-a', 'checkpoint_ns': ''}}, _checkpoint('a1'), {'run_id': 'run-1'}, {})
    saver.put(first_config, _checkpoint('a2'), {'run_id': 'run-2'}, {})

    saver.delete_for_runs(['run-2'])

    latest = saver.get_tuple({'configurable': {'thread_id': 'thread-a', 'checkpoint_ns': ''}})
    assert latest is not None
    assert latest.config['configurable']['checkpoint_id'] == 'a1'


def test_sqlite_copy_thread_replays_full_thread(tmp_path):
    db_path = tmp_path / 'copy-thread.db'
    set_checkpointer_config(None)
    reset_checkpointer()
    load_checkpointer_config_from_dict({'type': 'sqlite', 'connection_string': str(db_path)})

    saver = get_checkpointer()
    first_config = saver.put({'configurable': {'thread_id': 'source-thread', 'checkpoint_ns': ''}}, _checkpoint('b1'), {'run_id': 'copy-run'}, {})
    saver.put(first_config, _checkpoint('b2'), {'run_id': 'copy-run'}, {})

    saver.copy_thread('source-thread', 'target-thread')

    copied = list(saver.list({'configurable': {'thread_id': 'target-thread', 'checkpoint_ns': ''}}))
    assert [item.config['configurable']['checkpoint_id'] for item in copied] == ['b2', 'b1']


def test_sqlite_prune_keep_latest_keeps_single_latest_checkpoint(tmp_path):
    db_path = tmp_path / 'prune.db'
    set_checkpointer_config(None)
    reset_checkpointer()
    load_checkpointer_config_from_dict({'type': 'sqlite', 'connection_string': str(db_path)})

    saver = get_checkpointer()
    first_config = saver.put({'configurable': {'thread_id': 'thread-prune', 'checkpoint_ns': ''}}, _checkpoint('c1'), {'run_id': 'prune-run'}, {})
    saver.put(first_config, _checkpoint('c2'), {'run_id': 'prune-run'}, {})

    saver.prune(['thread-prune'], strategy='keep_latest')

    remaining = list(saver.list({'configurable': {'thread_id': 'thread-prune', 'checkpoint_ns': ''}}))
    assert len(remaining) == 1
    assert remaining[0].config['configurable']['checkpoint_id'] == 'c2'
