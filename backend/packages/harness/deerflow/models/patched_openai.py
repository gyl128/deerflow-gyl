"""Patched ChatOpenAI variants for gateway compatibility fixes.

When using Gemini with thinking enabled via an OpenAI-compatible gateway (e.g.
Vertex AI, Google AI Studio, or any proxy), the API requires that the
``thought_signature`` field on tool-call objects is echoed back verbatim in
every subsequent request.

The OpenAI-compatible gateway stores the raw tool-call dicts (including
``thought_signature``) in ``additional_kwargs["tool_calls"]``, but standard
``langchain_openai.ChatOpenAI`` only serialises the standard fields (``id``,
``type``, ``function``) into the outgoing payload, silently dropping the
signature.  That causes an HTTP 400 ``INVALID_ARGUMENT`` error:

    Unable to submit request because function call `<tool>` in the N. content
    block is missing a `thought_signature`.

This module fixes the problem by overriding ``_get_request_payload`` to
re-inject tool-call signatures back into the outgoing payload for any assistant
message that originally carried them.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from typing import Any

from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.messages.tool import tool_call_chunk
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_openai import ChatOpenAI


class PatchedChatOpenAI(ChatOpenAI):
    """ChatOpenAI with ``thought_signature`` preservation for Gemini thinking via OpenAI gateway.

    When using Gemini with thinking enabled via an OpenAI-compatible gateway,
    the API expects ``thought_signature`` to be present on tool-call objects in
    multi-turn conversations.  This patched version restores those signatures
    from ``AIMessage.additional_kwargs["tool_calls"]`` into the serialised
    request payload before it is sent to the API.

    Usage in ``config.yaml``::

        - name: gemini-2.5-pro-thinking
          display_name: Gemini 2.5 Pro (Thinking)
          use: deerflow.models.patched_openai:PatchedChatOpenAI
          model: google/gemini-2.5-pro-preview
          api_key: $GEMINI_API_KEY
          base_url: https://<your-openai-compat-gateway>/v1
          max_tokens: 16384
          supports_thinking: true
          supports_vision: true
          when_thinking_enabled:
            extra_body:
              thinking:
                type: enabled
    """

    def _get_request_payload(
        self,
        input_: LanguageModelInput,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        """Get request payload with ``thought_signature`` preserved on tool-call objects.

        Overrides the parent method to re-inject ``thought_signature`` fields
        on tool-call objects that were stored in
        ``additional_kwargs["tool_calls"]`` by LangChain but dropped during
        serialisation.
        """
        # Capture the original LangChain messages *before* conversion so we can
        # access fields that the serialiser might drop.
        original_messages = self._convert_input(input_).to_messages()

        # Obtain the base payload from the parent implementation.
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)

        payload_messages = payload.get("messages", [])

        if len(payload_messages) == len(original_messages):
            for payload_msg, orig_msg in zip(payload_messages, original_messages):
                if payload_msg.get("role") == "assistant" and isinstance(orig_msg, AIMessage):
                    _restore_tool_call_signatures(payload_msg, orig_msg)
        else:
            # Fallback: match assistant-role entries positionally against AIMessages.
            ai_messages = [m for m in original_messages if isinstance(m, AIMessage)]
            assistant_payloads = [
                (i, m) for i, m in enumerate(payload_messages) if m.get("role") == "assistant"
            ]
            for (_, payload_msg), ai_msg in zip(assistant_payloads, ai_messages):
                _restore_tool_call_signatures(payload_msg, ai_msg)

        return payload

    def _create_chat_result(self, response: dict | Any, generation_info: dict | None = None) -> ChatResult:
        """Create a chat result without leaking Pydantic warnings from gateway responses.

        Some OpenAI-compatible gateways return assistant ``content`` as a list
        of blocks instead of a plain string. The OpenAI SDK response models can
        still carry that payload, but ``model_dump()`` emits serializer
        warnings because the typed field expects ``str``. LangChain calls
        ``model_dump()`` before converting the payload into an ``AIMessage``,
        so we intercept that step here and perform a warning-free dump while
        preserving structured content blocks.
        """
        if isinstance(response, dict) or not hasattr(response, "model_dump"):
            return super()._create_chat_result(response, generation_info)

        response_dict = _response_model_to_dict(response)
        result = super()._create_chat_result(response_dict, generation_info)

        choices = getattr(response, "choices", None)
        if choices:
            message = choices[0].message
            if hasattr(message, "parsed"):
                result.generations[0].message.additional_kwargs["parsed"] = message.parsed
            if hasattr(message, "refusal"):
                result.generations[0].message.additional_kwargs["refusal"] = message.refusal

        return result


class QwenPoolChatOpenAI(PatchedChatOpenAI):
    """ChatOpenAI variant that avoids fragile streaming behavior in qwenpool.

    qwenpool is reliable for non-stream chat-completion requests, but its
    streaming path can produce incomplete tool-calling behavior under LangGraph.
    To keep agent runs stable, this provider always performs a non-stream
    request at the transport layer and converts the complete response back into
    a single LangChain ``ChatGenerationChunk``.
    """

    def _stream(
        self,
        messages,
        stop: list[str] | None = None,
        run_manager=None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        kwargs.pop("stream", None)
        kwargs.pop("stream_options", None)
        result = super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
        chunk = _chat_result_to_generation_chunk(result)
        if chunk is None:
            return
        if run_manager:
            run_manager.on_llm_new_token(chunk.text, chunk=chunk)
        yield chunk

    async def _astream(
        self,
        messages,
        stop: list[str] | None = None,
        run_manager=None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        kwargs.pop("stream", None)
        kwargs.pop("stream_options", None)
        result = await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)
        chunk = _chat_result_to_generation_chunk(result)
        if chunk is None:
            return
        if run_manager:
            await run_manager.on_llm_new_token(chunk.text, chunk=chunk)
        yield chunk


def _flatten_ai_content(content: Any) -> str:
    """Convert OpenAI-style content blocks into plain text."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return "" if content is None else str(content)

    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
            continue
        if not isinstance(block, dict):
            continue
        if block.get("type") != "text":
            continue
        text = block.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def _response_model_to_dict(response: Any) -> dict[str, Any]:
    """Dump SDK response models without emitting serializer warnings.

    OpenAI-compatible gateways sometimes populate ``message.content`` with
    block lists. We want to preserve that exact data while avoiding noisy
    warnings from Pydantic's serializer.
    """
    return response.model_dump(warnings=False)


def _chat_result_to_generation_chunk(result: ChatResult) -> ChatGenerationChunk | None:
    """Convert a full non-stream result into one final generation chunk."""
    if not result.generations:
        return None

    generation = result.generations[0]
    message = generation.message
    if not isinstance(message, AIMessage):
        return None

    text_content = _flatten_ai_content(message.content)
    tool_call_chunks = []
    for index, call in enumerate(message.tool_calls):
        args = call.get("args")
        if isinstance(args, str):
            serialized_args = args
        else:
            serialized_args = json.dumps(args or {}, ensure_ascii=False, separators=(",", ":"))
        tool_call_chunks.append(
            tool_call_chunk(
                name=call.get("name"),
                args=serialized_args,
                id=call.get("id"),
                index=index,
            )
        )

    message_chunk = AIMessageChunk(
        content=text_content,
        additional_kwargs=dict(message.additional_kwargs),
        response_metadata=dict(message.response_metadata),
        id=getattr(message, "id", None),
        tool_call_chunks=tool_call_chunks,
        usage_metadata=getattr(message, "usage_metadata", None),
        chunk_position="last",
    )
    return ChatGenerationChunk(
        message=message_chunk,
        generation_info=generation.generation_info,
        text=text_content,
    )


def _restore_tool_call_signatures(payload_msg: dict, orig_msg: AIMessage) -> None:
    """Re-inject ``thought_signature`` onto tool-call objects in *payload_msg*.

    When the Gemini OpenAI-compatible gateway returns a response with function
    calls, each tool-call object may carry a ``thought_signature``.  LangChain
    stores the raw tool-call dicts in ``additional_kwargs["tool_calls"]`` but
    only serialises the standard fields (``id``, ``type``, ``function``) into
    the outgoing payload, silently dropping the signature.

    This function matches raw tool-call entries (by ``id``, falling back to
    positional order) and copies the signature back onto the serialised
    payload entries.
    """
    raw_tool_calls: list[dict] = orig_msg.additional_kwargs.get("tool_calls") or []
    payload_tool_calls: list[dict] = payload_msg.get("tool_calls") or []

    if not raw_tool_calls or not payload_tool_calls:
        return

    # Build an id → raw_tc lookup for efficient matching.
    raw_by_id: dict[str, dict] = {}
    for raw_tc in raw_tool_calls:
        tc_id = raw_tc.get("id")
        if tc_id:
            raw_by_id[tc_id] = raw_tc

    for idx, payload_tc in enumerate(payload_tool_calls):
        # Try matching by id first, then fall back to positional.
        raw_tc = raw_by_id.get(payload_tc.get("id", ""))
        if raw_tc is None and idx < len(raw_tool_calls):
            raw_tc = raw_tool_calls[idx]

        if raw_tc is None:
            continue

        # The gateway may use either snake_case or camelCase.
        sig = raw_tc.get("thought_signature") or raw_tc.get("thoughtSignature")
        if sig:
            payload_tc["thought_signature"] = sig
