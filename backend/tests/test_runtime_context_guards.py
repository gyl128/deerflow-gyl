from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage

from deerflow.agents.middlewares.loop_detection_middleware import LoopDetectionMiddleware
from deerflow.agents.middlewares.memory_middleware import MemoryMiddleware
from deerflow.agents.middlewares.uploads_middleware import UploadsMiddleware


def test_uploads_middleware_handles_missing_runtime_context(tmp_path: Path):
    middleware = UploadsMiddleware(base_dir=str(tmp_path))
    runtime = MagicMock()
    runtime.context = None

    state = {
        "messages": [
            HumanMessage(
                content="请分析附件",
                additional_kwargs={
                    "files": [
                        {
                            "filename": "report.txt",
                            "size": 12,
                            "path": "/mnt/user-data/uploads/report.txt",
                        }
                    ]
                },
            )
        ]
    }

    result = middleware.before_agent(state, runtime)

    assert result is not None
    updated_message = result["messages"][-1]
    assert "<uploaded_files>" in updated_message.content
    assert "report.txt" in updated_message.content
    assert "请分析附件" in updated_message.content


def test_memory_middleware_skips_cleanly_when_runtime_context_missing(monkeypatch):
    middleware = MemoryMiddleware()
    runtime = MagicMock()
    runtime.context = None

    monkeypatch.setattr(
        "deerflow.agents.middlewares.memory_middleware.get_memory_config",
        lambda: SimpleNamespace(enabled=True),
    )
    queue = MagicMock()
    monkeypatch.setattr(
        "deerflow.agents.middlewares.memory_middleware.get_memory_queue",
        lambda: queue,
    )

    state = {
        "messages": [
            HumanMessage(content="你好"),
            AIMessage(content="收到"),
        ]
    }

    assert middleware.after_agent(state, runtime) is None
    queue.add.assert_not_called()


def test_loop_detection_uses_default_thread_when_runtime_context_missing():
    middleware = LoopDetectionMiddleware(warn_threshold=2)
    runtime = MagicMock()
    runtime.context = None

    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[{"name": "bash", "id": "call_ls", "args": {"command": "ls"}}],
            )
        ]
    }

    assert middleware._apply(state, runtime) is None
    assert "default" in middleware._history
