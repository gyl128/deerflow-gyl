from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.channels.message_bus import InboundMessage, MessageBus, OutboundMessage
from app.channels.weixin import (
    WeixinAccount,
    WeixinChannel,
    WeixinError,
    WeixinStateStore,
    extract_text_from_message,
    is_supported_direct_text_message,
    login_via_qr,
    render_weixin_text,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestWeixinStateStore:
    def test_persists_account_cursor_and_context_tokens(self, tmp_path: Path):
        store = WeixinStateStore(tmp_path)
        store.save_account(WeixinAccount(account_id="bot-1", token="secret", base_url="https://example.test"))
        store.save_cursor("cursor-1")
        store.set_context_token("u@im.wechat", "ctx-1")
        store.pause_session(60)
        store.set_last_inbound_at(123.0)
        store.set_last_error("boom")

        reloaded = WeixinStateStore(tmp_path)
        account = reloaded.load_account()
        assert account is not None
        assert account.account_id == "bot-1"
        assert reloaded.load_cursor() == "cursor-1"
        assert reloaded.get_context_token("u@im.wechat") == "ctx-1"
        status = reloaded.status_snapshot()
        assert status["configured"] is True
        assert status["logged_in"] is False
        assert status["paused"] is True
        assert status["relogin_required"] is True
        assert status["last_inbound_at"] == 123.0
        assert status["last_error"] == "boom"

    def test_exposes_relogin_hint_when_session_is_expired(self, tmp_path: Path):
        store = WeixinStateStore(tmp_path)
        store.save_account(WeixinAccount(account_id="bot-1", token="secret", base_url="https://example.test"))
        store.pause_session(60)

        status = store.status_snapshot()
        assert status["logged_in"] is False
        assert status["relogin_required"] is True
        assert "weixin-login.sh" in status["relogin_hint"]
        assert "session expired" in status["last_error"].lower()


class TestWeixinMessageParsing:
    def test_extract_text_from_message(self):
        message = {
            "from_user_id": "u@im.wechat",
            "item_list": [{"type": 1, "text_item": {"text": "hello"}}],
        }
        assert extract_text_from_message(message) == "hello"

    def test_supported_message_requires_private_text(self):
        assert is_supported_direct_text_message(
            {
                "from_user_id": "u@im.wechat",
                "item_list": [{"type": 1, "text_item": {"text": "hello"}}],
            }
        )
        assert not is_supported_direct_text_message(
            {
                "from_user_id": "u@im.wechat",
                "group_id": "group-1",
                "item_list": [{"type": 1, "text_item": {"text": "hello"}}],
            }
        )
        assert not is_supported_direct_text_message(
            {
                "from_user_id": "u@im.wechat",
                "item_list": [{"type": 2, "image_item": {}}],
            }
        )

    def test_render_weixin_text_strips_markdown_markers(self):
        rendered = render_weixin_text(
            "# Title\n\n**bold** and *italic*\n\n- item\n\n`code`\n\n[link](https://example.com)"
        )
        assert "# " not in rendered
        assert "**" not in rendered
        assert "*italic*" not in rendered
        assert "bold and italic" in rendered
        assert "- item" in rendered
        assert "code" in rendered
        assert "link: https://example.com" in rendered
        assert "*" not in rendered


class TestWeixinLogin:
    def test_login_via_qr_saves_account(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        responses = iter(
            [
                {"status": "wait"},
                {
                    "status": "confirmed",
                    "bot_token": "token-1",
                    "ilink_bot_id": "bot@im.bot",
                    "baseurl": "https://bridge.test",
                    "ilink_user_id": "owner@im.wechat",
                },
            ]
        )

        class FakeClient:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            async def get_bot_qrcode(self, **kwargs):
                return {"qrcode": "qr-1", "qrcode_img_content": "https://qr.example/1"}

            async def get_qrcode_status(self, **kwargs):
                return next(responses)

        monkeypatch.setattr("app.channels.weixin.WeixinApiClient", FakeClient)
        monkeypatch.setattr("app.channels.weixin._print_ascii_qr", lambda content: None)

        account = _run(login_via_qr(base_url="https://bridge.test", state_dir=tmp_path, timeout_ms=5000))
        assert account.token == "token-1"

        store = WeixinStateStore(tmp_path)
        reloaded = store.load_account()
        assert reloaded is not None
        assert reloaded.token == "token-1"
        assert reloaded.user_id == "owner@im.wechat"


class TestWeixinChannel:
    def test_start_requires_logged_in_account(self, tmp_path: Path):
        async def go():
            bus = MessageBus()
            channel = WeixinChannel(bus=bus, config={"state_dir": str(tmp_path)})
            with pytest.raises(WeixinError, match="weixin-login.sh"):
                await channel.start()
            assert channel.is_running is False
            assert channel.get_status_snapshot()["configured"] is False
            assert channel.get_status_snapshot()["relogin_required"] is True

        _run(go())

    def test_handle_inbound_message_publishes_direct_text(self, tmp_path: Path):
        async def go():
            bus = MessageBus()
            channel = WeixinChannel(bus=bus, config={"state_dir": str(tmp_path)})

            await channel._handle_inbound_message(
                {
                    "message_id": 1,
                    "from_user_id": "friend@im.wechat",
                    "context_token": "ctx-1",
                    "item_list": [{"type": 1, "text_item": {"text": "hello from wx"}}],
                }
            )
            seen = await asyncio.wait_for(bus.get_inbound(), timeout=1)
            assert seen.chat_id == "friend@im.wechat"
            assert seen.topic_id == "friend@im.wechat"
            assert seen.text == "hello from wx"
            assert channel.get_status_snapshot()["last_inbound_at"] is not None

        _run(go())

    def test_send_requires_context_token(self, tmp_path: Path):
        async def go():
            bus = MessageBus()
            channel = WeixinChannel(bus=bus, config={"state_dir": str(tmp_path)})
            channel._state.save_account(WeixinAccount(account_id="bot-1", token="token-1", base_url="https://bridge.test"))
            with pytest.raises(WeixinError, match="context token"):
                await channel.send(OutboundMessage(channel_name="weixin", chat_id="friend@im.wechat", thread_id="t1", text="reply"))

        _run(go())

    def test_send_uses_cached_context_token(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        async def go():
            bus = MessageBus()
            channel = WeixinChannel(bus=bus, config={"state_dir": str(tmp_path)})
            channel._state.save_account(WeixinAccount(account_id="bot-1", token="token-1", base_url="https://bridge.test"))
            channel._state.set_context_token("friend@im.wechat", "ctx-1")

            sent = AsyncMock(return_value={})

            class FakeClient:
                def __init__(self, **kwargs):
                    self.kwargs = kwargs

                async def send_text_message(self, **kwargs):
                    return await sent(**kwargs)

            monkeypatch.setattr("app.channels.weixin.WeixinApiClient", FakeClient)
            await channel.send(
                OutboundMessage(
                    channel_name="weixin",
                    chat_id="friend@im.wechat",
                    thread_id="t1",
                    text="**reply**\n\n[doc](https://example.com)",
                )
            )
            sent.assert_awaited_once_with(
                to_user_id="friend@im.wechat",
                text="reply\n\ndoc: https://example.com",
                context_token="ctx-1",
            )

        _run(go())
