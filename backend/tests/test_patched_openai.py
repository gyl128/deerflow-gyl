"""Tests for deerflow.models.patched_openai compatibility patches."""

from __future__ import annotations

import json
import warnings

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import BaseModel

from deerflow.models.patched_openai import (
    PatchedChatOpenAI,
    _chat_result_to_generation_chunk,
    _flatten_ai_content,
    _response_model_to_dict,
    _restore_tool_call_signatures,
)


RAW_TC_SIGNED = {
    "id": "call_1",
    "type": "function",
    "function": {"name": "web_fetch", "arguments": '{"url":"http://example.com"}'},
    "thought_signature": "SIG_A==",
}

RAW_TC_UNSIGNED = {
    "id": "call_2",
    "type": "function",
    "function": {"name": "bash", "arguments": '{"cmd":"ls"}'},
}

PAYLOAD_TC_1 = {
    "type": "function",
    "id": "call_1",
    "function": {"name": "web_fetch", "arguments": '{"url":"http://example.com"}'},
}

PAYLOAD_TC_2 = {
    "type": "function",
    "id": "call_2",
    "function": {"name": "bash", "arguments": '{"cmd":"ls"}'},
}


def _ai_msg_with_raw_tool_calls(raw_tool_calls: list[dict]) -> AIMessage:
    return AIMessage(content="", additional_kwargs={"tool_calls": raw_tool_calls})


def test_tool_call_signature_restored_by_id():
    payload_msg = {"role": "assistant", "content": None, "tool_calls": [PAYLOAD_TC_1.copy()]}
    orig = _ai_msg_with_raw_tool_calls([RAW_TC_SIGNED])

    _restore_tool_call_signatures(payload_msg, orig)

    assert payload_msg["tool_calls"][0]["thought_signature"] == "SIG_A=="


def test_tool_call_signature_for_parallel_calls():
    payload_msg = {
        "role": "assistant",
        "content": None,
        "tool_calls": [PAYLOAD_TC_1.copy(), PAYLOAD_TC_2.copy()],
    }
    orig = _ai_msg_with_raw_tool_calls([RAW_TC_SIGNED, RAW_TC_UNSIGNED])

    _restore_tool_call_signatures(payload_msg, orig)

    assert payload_msg["tool_calls"][0]["thought_signature"] == "SIG_A=="
    assert "thought_signature" not in payload_msg["tool_calls"][1]


def test_tool_call_signature_camel_case():
    raw_camel = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "web_fetch", "arguments": "{}"},
        "thoughtSignature": "SIG_CAMEL==",
    }
    payload_msg = {"role": "assistant", "content": None, "tool_calls": [PAYLOAD_TC_1.copy()]}
    orig = _ai_msg_with_raw_tool_calls([raw_camel])

    _restore_tool_call_signatures(payload_msg, orig)

    assert payload_msg["tool_calls"][0]["thought_signature"] == "SIG_CAMEL=="


def test_tool_call_signature_positional_fallback():
    raw_no_id = {
        "type": "function",
        "function": {"name": "web_fetch", "arguments": "{}"},
        "thought_signature": "SIG_POS==",
    }
    payload_tc = {
        "type": "function",
        "id": "call_99",
        "function": {"name": "web_fetch", "arguments": "{}"},
    }
    payload_msg = {"role": "assistant", "content": None, "tool_calls": [payload_tc]}
    orig = _ai_msg_with_raw_tool_calls([raw_no_id])

    _restore_tool_call_signatures(payload_msg, orig)

    assert payload_tc["thought_signature"] == "SIG_POS=="


def test_tool_call_no_raw_tool_calls_is_noop():
    payload_msg = {"role": "assistant", "content": None, "tool_calls": [PAYLOAD_TC_1.copy()]}
    orig = AIMessage(content="", additional_kwargs={})

    _restore_tool_call_signatures(payload_msg, orig)

    assert "thought_signature" not in payload_msg["tool_calls"][0]


def test_tool_call_no_payload_tool_calls_is_noop():
    payload_msg = {"role": "assistant", "content": "just text"}
    orig = _ai_msg_with_raw_tool_calls([RAW_TC_SIGNED])

    _restore_tool_call_signatures(payload_msg, orig)

    assert "tool_calls" not in payload_msg


def test_tool_call_unsigned_raw_entries_is_noop():
    payload_msg = {"role": "assistant", "content": None, "tool_calls": [PAYLOAD_TC_2.copy()]}
    orig = _ai_msg_with_raw_tool_calls([RAW_TC_UNSIGNED])

    _restore_tool_call_signatures(payload_msg, orig)

    assert "thought_signature" not in payload_msg["tool_calls"][0]


def test_tool_call_multiple_sequential_signatures():
    raw_tc_a = {
        "id": "call_a",
        "type": "function",
        "function": {"name": "check_flight", "arguments": "{}"},
        "thought_signature": "SIG_STEP1==",
    }
    raw_tc_b = {
        "id": "call_b",
        "type": "function",
        "function": {"name": "book_taxi", "arguments": "{}"},
        "thought_signature": "SIG_STEP2==",
    }
    payload_tc_a = {"type": "function", "id": "call_a", "function": {"name": "check_flight", "arguments": "{}"}}
    payload_tc_b = {"type": "function", "id": "call_b", "function": {"name": "book_taxi", "arguments": "{}"}}
    payload_msg = {"role": "assistant", "content": None, "tool_calls": [payload_tc_a, payload_tc_b]}
    orig = _ai_msg_with_raw_tool_calls([raw_tc_a, raw_tc_b])

    _restore_tool_call_signatures(payload_msg, orig)

    assert payload_tc_a["thought_signature"] == "SIG_STEP1=="
    assert payload_tc_b["thought_signature"] == "SIG_STEP2=="


def test_flatten_ai_content_handles_openai_blocks():
    content = [
        {"type": "text", "text": "first"},
        {"type": "tool_result", "text": "ignored"},
        "second",
    ]

    assert _flatten_ai_content(content) == "firstsecond"


def test_chat_result_to_generation_chunk_preserves_tool_calls():
    message = AIMessage(
        content=[{"type": "text", "text": "let me check"}],
        tool_calls=[
            {
                "name": "web_search",
                "args": {"query": "polymarket"},
                "id": "call_1",
                "type": "tool_call",
            }
        ],
        response_metadata={"finish_reason": "tool_calls"},
    )
    result = ChatResult(
        generations=[
            ChatGeneration(
                message=message,
                generation_info={"finish_reason": "tool_calls"},
            )
        ]
    )

    chunk = _chat_result_to_generation_chunk(result)

    assert chunk is not None
    assert chunk.text == "let me check"
    assert chunk.generation_info == {"finish_reason": "tool_calls"}
    assert chunk.message.tool_calls[0]["name"] == "web_search"
    assert json.loads(chunk.message.tool_call_chunks[0]["args"]) == {"query": "polymarket"}
    assert chunk.message.chunk_position == "last"


class _FakeMessage(BaseModel):
    role: str
    content: str | None = None
    tool_calls: list[dict] | None = None
    parsed: dict | None = None


class _FakeChoice(BaseModel):
    message: _FakeMessage
    finish_reason: str | None = None


class _FakeUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class _FakeResponse(BaseModel):
    choices: list[_FakeChoice]
    usage: _FakeUsage | None = None
    model: str | None = None
    id: str | None = None


def _fake_openai_response() -> _FakeResponse:
    return _FakeResponse.model_construct(
        choices=[
            _FakeChoice.model_construct(
                message=_FakeMessage.model_construct(
                    role="assistant",
                    content=[{"type": "text", "text": "structured answer"}],
                    tool_calls=[
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "web_search",
                                "arguments": '{"query":"polymarket"}',
                            },
                        }
                    ],
                    parsed={"structured": True},
                ),
                finish_reason="tool_calls",
            )
        ],
        usage=_FakeUsage.model_construct(
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
        ),
        model="openai:coder-model",
        id="resp_1",
    )


def test_response_model_to_dict_suppresses_block_content_serializer_warnings():
    response = _fake_openai_response()

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        dumped = _response_model_to_dict(response)

    assert dumped["choices"][0]["message"]["content"] == [{"type": "text", "text": "structured answer"}]


def test_patched_chat_result_handles_block_content_without_warning():
    response = _fake_openai_response()
    model = PatchedChatOpenAI(
        model="openai:coder-model",
        api_key="test-key",
        base_url="http://example.com/v1",
    )

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        result = model._create_chat_result(response)

    message = result.generations[0].message
    assert message.content == [{"type": "text", "text": "structured answer"}]
    assert message.tool_calls[0]["name"] == "web_search"
    assert message.tool_calls[0]["args"] == {"query": "polymarket"}
    assert message.additional_kwargs["parsed"] == {"structured": True}
    assert message.usage_metadata is not None
    assert message.usage_metadata["input_tokens"] == 10
    assert message.usage_metadata["output_tokens"] == 5
    assert message.usage_metadata["total_tokens"] == 15
