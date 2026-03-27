"""Weixin channel using Tencent's official local bridge HTTP API."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from app.channels.base import Channel
from app.channels.message_bus import InboundMessage, OutboundMessage
from deerflow.config.paths import get_paths

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
DEFAULT_BOT_TYPE = "3"
DEFAULT_POLL_TIMEOUT_MS = 35000
DEFAULT_LOGIN_TIMEOUT_MS = 480000
MAX_QR_REFRESH_COUNT = 3
SESSION_EXPIRED_ERRCODE = -14
SESSION_PAUSE_DURATION_SECONDS = 3600


def _default_relogin_hint() -> str:
    repo_root = Path(__file__).resolve().parents[3]
    return f"Run {repo_root / 'scripts' / 'weixin-login.sh'} to sign in again."


def _session_expired_message() -> str:
    return f"Weixin session expired. {_default_relogin_hint()}"


class WeixinError(RuntimeError):
    """Base exception for Weixin channel errors."""


class WeixinSessionExpiredError(WeixinError):
    """Raised when the bridge reports an expired session."""


@dataclass(slots=True)
class WeixinAccount:
    account_id: str
    token: str
    base_url: str = DEFAULT_BASE_URL
    created_at: float | None = None
    user_id: str | None = None


class WeixinStateStore:
    """JSON-file-backed single-account state store for Weixin."""

    def __init__(self, root: Path | None = None) -> None:
        base = root or (get_paths().base_dir / "weixin")
        self._root = Path(base)
        self._root.mkdir(parents=True, exist_ok=True)
        self._account_path = self._root / "account.json"
        self._cursor_path = self._root / "get_updates_buf"
        self._session_path = self._root / "session.json"

    @property
    def root(self) -> Path:
        return self._root

    def load_account(self) -> WeixinAccount | None:
        data = self._read_json(self._account_path)
        if not data:
            return None
        token = str(data.get("token", "")).strip()
        if not token:
            return None
        return WeixinAccount(
            account_id=str(data.get("account_id", "default")).strip() or "default",
            token=token,
            base_url=str(data.get("base_url", DEFAULT_BASE_URL)).strip() or DEFAULT_BASE_URL,
            created_at=float(data["created_at"]) if data.get("created_at") is not None else None,
            user_id=str(data["user_id"]).strip() if data.get("user_id") else None,
        )

    def save_account(self, account: WeixinAccount) -> None:
        payload = {
            "account_id": account.account_id,
            "token": account.token,
            "base_url": account.base_url,
            "created_at": account.created_at or time.time(),
            "user_id": account.user_id,
        }
        self._write_json(self._account_path, payload)
        self._merge_session({"logged_in": True, "session_expired": False, "paused_until": 0.0, "last_error": None})

    def clear_account(self) -> None:
        self._account_path.unlink(missing_ok=True)
        self._merge_session({"logged_in": False, "session_expired": False})

    def load_cursor(self) -> str:
        if not self._cursor_path.exists():
            return ""
        try:
            return self._cursor_path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def save_cursor(self, cursor: str) -> None:
        self._cursor_path.write_text(cursor or "", encoding="utf-8")

    def load_session(self) -> dict[str, Any]:
        session = self._read_json(self._session_path) or {}
        session.setdefault("context_tokens", {})
        session.setdefault("logged_in", self._account_path.exists())
        session.setdefault("polling", False)
        session.setdefault("paused_until", 0.0)
        session.setdefault("session_expired", False)
        session.setdefault("last_inbound_at", None)
        session.setdefault("last_error", None)
        return session

    def set_context_token(self, user_id: str, token: str) -> None:
        session = self.load_session()
        context_tokens = session.setdefault("context_tokens", {})
        context_tokens[user_id] = token
        self._write_json(self._session_path, session)

    def get_context_token(self, user_id: str) -> str | None:
        session = self.load_session()
        token = session.get("context_tokens", {}).get(user_id)
        return token if isinstance(token, str) and token else None

    def set_polling(self, polling: bool) -> None:
        self._merge_session({"polling": polling})

    def set_last_inbound_at(self, ts: float | None = None) -> None:
        self._merge_session({"last_inbound_at": ts or time.time()})

    def set_last_error(self, message: str | None) -> None:
        self._merge_session({"last_error": message})

    def pause_session(self, seconds: int = SESSION_PAUSE_DURATION_SECONDS) -> None:
        self._merge_session(
            {
                "paused_until": time.time() + seconds,
                "session_expired": True,
                "logged_in": False,
                "last_error": _session_expired_message(),
            }
        )

    def clear_pause(self) -> None:
        self._merge_session({"paused_until": 0.0, "session_expired": False})

    def status_snapshot(self) -> dict[str, Any]:
        account = self.load_account()
        session = self.load_session()
        paused_until = float(session.get("paused_until") or 0.0)
        now = time.time()
        paused = paused_until > now
        if not paused and paused_until:
            self.clear_pause()
            paused_until = 0.0
            session = self.load_session()
        session_expired = bool(session.get("session_expired"))
        relogin_required = account is None or session_expired
        return {
            "configured": account is not None,
            "logged_in": bool(session.get("logged_in")) and account is not None and not session_expired,
            "polling": bool(session.get("polling")),
            "paused": paused,
            "paused_until": paused_until or None,
            "relogin_required": relogin_required,
            "relogin_hint": _default_relogin_hint() if relogin_required else None,
            "last_inbound_at": session.get("last_inbound_at"),
            "last_error": session.get("last_error"),
        }

    def _merge_session(self, updates: dict[str, Any]) -> None:
        session = self.load_session()
        session.update(updates)
        self._write_json(self._session_path, session)

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("Failed to load Weixin state file %s", path)
            return None

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


class WeixinApiClient:
    """Minimal async client for the Tencent Weixin bridge API."""

    def __init__(self, *, base_url: str = DEFAULT_BASE_URL, token: str | None = None, timeout_ms: int = DEFAULT_POLL_TIMEOUT_MS) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_ms = timeout_ms

    async def get_bot_qrcode(self, *, bot_type: str = DEFAULT_BOT_TYPE) -> dict[str, Any]:
        url = f"{self.base_url}/ilink/bot/get_bot_qrcode"
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(url, params={"bot_type": bot_type}, headers=self._build_headers())
        response.raise_for_status()
        return response.json()

    async def get_qrcode_status(self, *, qrcode: str) -> dict[str, Any]:
        url = f"{self.base_url}/ilink/bot/get_qrcode_status"
        timeout = httpx.Timeout(self.timeout_ms / 1000 + 5.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(
                    url,
                    params={"qrcode": qrcode},
                    headers={"iLink-App-ClientVersion": "1", **self._build_headers()},
                )
        except httpx.TimeoutException:
            return {"status": "wait"}
        response.raise_for_status()
        return response.json()

    async def get_updates(self, *, cursor: str) -> dict[str, Any]:
        payload = {"get_updates_buf": cursor, "base_info": self._base_info()}
        try:
            data = await self._post_json("ilink/bot/getupdates", payload, timeout_ms=self.timeout_ms)
        except httpx.TimeoutException:
            return {"ret": 0, "msgs": [], "get_updates_buf": cursor}
        self._raise_if_session_expired(data)
        return data

    async def send_text_message(self, *, to_user_id: str, text: str, context_token: str) -> dict[str, Any]:
        payload = {
            "msg": {
                "from_user_id": "",
                "to_user_id": to_user_id,
                "client_id": f"deerflow-weixin-{secrets.token_hex(8)}",
                "message_type": 2,
                "message_state": 2,
                "item_list": [{"type": 1, "text_item": {"text": text}}],
                "context_token": context_token,
            },
            "base_info": self._base_info(),
        }
        data = await self._post_json("ilink/bot/sendmessage", payload, timeout_ms=15000)
        self._raise_if_session_expired(data)
        return data

    async def _post_json(self, endpoint: str, payload: dict[str, Any], *, timeout_ms: int) -> dict[str, Any]:
        url = f"{self.base_url}/{endpoint}"
        timeout = httpx.Timeout(timeout_ms / 1000 + 5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, json=payload, headers=self._build_headers())
        response.raise_for_status()
        if not response.text.strip():
            return {}
        return response.json()

    @staticmethod
    def _base_info() -> dict[str, str]:
        return {"channel_version": "deerflow-weixin-v1"}

    @staticmethod
    def _wechat_uin() -> str:
        return base64.b64encode(str(secrets.randbelow(2**32)).encode("utf-8")).decode("utf-8")

    def _build_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "X-WECHAT-UIN": self._wechat_uin(),
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        route_tag = os.getenv("WEIXIN_ROUTE_TAG", "").strip()
        if route_tag:
            headers["SKRouteTag"] = route_tag
        return headers

    @staticmethod
    def _raise_if_session_expired(data: dict[str, Any]) -> None:
        errcode = data.get("errcode")
        ret = data.get("ret")
        if errcode == SESSION_EXPIRED_ERRCODE or ret == SESSION_EXPIRED_ERRCODE:
            raise WeixinSessionExpiredError("Weixin session expired")


def extract_text_from_message(message: dict[str, Any]) -> str:
    for item in message.get("item_list") or []:
        if item.get("type") == 1:
            text = item.get("text_item", {}).get("text", "")
            if isinstance(text, str):
                return text
    return ""


def is_supported_direct_text_message(message: dict[str, Any]) -> bool:
    if message.get("group_id"):
        return False
    from_user_id = message.get("from_user_id")
    if not isinstance(from_user_id, str) or not from_user_id.endswith("@im.wechat"):
        return False
    return bool(extract_text_from_message(message))


def render_weixin_text(text: str) -> str:
    """Render DeerFlow markdown-ish output into Weixin-friendly plain text."""
    normalized = text.replace("\r\n", "\n").strip()
    if not normalized:
        return ""

    normalized = re.sub(r"```[a-zA-Z0-9_-]*\n", "[code]\n", normalized)
    normalized = normalized.replace("```", "\n[/code]")
    normalized = re.sub(r"`([^`]+)`", r"\1", normalized)
    normalized = re.sub(r"^\s{0,3}#{1,6}\s*", "", normalized, flags=re.MULTILINE)
    normalized = re.sub(r"^\s*>\s?", "Quote: ", normalized, flags=re.MULTILINE)
    normalized = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r"Image: \2", normalized)
    normalized = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1: \2", normalized)
    normalized = re.sub(r"(?<!\*)\*\*([^*]+)\*\*(?!\*)", r"\1", normalized)
    normalized = re.sub(r"(?<!_)__([^_]+)__(?!_)", r"\1", normalized)
    normalized = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", normalized)
    normalized = re.sub(r"(?<!_)_([^_\n]+)_(?!_)", r"\1", normalized)
    normalized = re.sub(r"~~([^~]+)~~", r"\1", normalized)
    normalized = normalized.replace("**", "").replace("__", "").replace("~~", "")
    normalized = re.sub(r"^\s*#{1,6}(?=\S)", "", normalized, flags=re.MULTILINE)
    normalized = re.sub(r"(?m)^(\s*)[*+]\s+", r"\1- ", normalized)
    normalized = re.sub(r"(?<=\s)\*(?=\S)", "", normalized)
    normalized = re.sub(r"(?<=\S)\*(?=\s)", "", normalized)
    normalized = re.sub(r"^\s*[-*]\s+", "- ", normalized, flags=re.MULTILINE)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


class WeixinChannel(Channel):
    """DeerFlow native Weixin channel backed by the Tencent bridge API."""

    def __init__(self, bus, config: dict[str, Any]) -> None:
        super().__init__(name="weixin", bus=bus, config=config)
        state_dir = config.get("state_dir")
        self._state = WeixinStateStore(Path(state_dir) if isinstance(state_dir, str) and state_dir else None)
        self._poll_task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._running:
            return
        self._state.set_last_error(None)
        account = self._state.load_account()
        if account is None:
            logger.warning("[Weixin] channel enabled but not logged in; run scripts/weixin-login.sh first")
            self._state._merge_session({"logged_in": False, "polling": False, "last_error": _default_relogin_hint()})
            raise WeixinError(_default_relogin_hint())
        self._running = True
        self.bus.subscribe_outbound(self._on_outbound)
        self._state._merge_session({"logged_in": True})
        self._poll_task = asyncio.create_task(self._poll_loop(account))
        logger.info("[Weixin] channel started (account_id=%s)", account.account_id)

    async def stop(self) -> None:
        self._running = False
        self.bus.unsubscribe_outbound(self._on_outbound)
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        self._state.set_polling(False)
        logger.info("[Weixin] channel stopped")

    async def send(self, msg: OutboundMessage) -> None:
        account = self._state.load_account()
        if account is None:
            raise WeixinError("Weixin account is not logged in")
        status = self._state.status_snapshot()
        if status["paused"]:
            raise WeixinSessionExpiredError(_session_expired_message())
        context_token = self._state.get_context_token(msg.chat_id)
        if not context_token:
            raise WeixinError(f"Weixin context token is missing for user {msg.chat_id}")
        rendered_text = render_weixin_text(msg.text)
        if not rendered_text:
            rendered_text = "(No response)"

        client = WeixinApiClient(base_url=account.base_url, token=account.token, timeout_ms=int(self.config.get("poll_timeout_ms", DEFAULT_POLL_TIMEOUT_MS)))
        try:
            await client.send_text_message(to_user_id=msg.chat_id, text=rendered_text, context_token=context_token)
            self._state.set_last_error(None)
        except WeixinSessionExpiredError:
            self._state.pause_session()
            logger.warning("[Weixin] outbound send blocked because session expired; %s", _default_relogin_hint())
            raise
        except Exception as exc:
            self._state.set_last_error(str(exc))
            raise

    def get_status_snapshot(self) -> dict[str, Any]:
        return self._state.status_snapshot()

    async def _poll_loop(self, account: WeixinAccount) -> None:
        client = WeixinApiClient(
            base_url=account.base_url,
            token=account.token,
            timeout_ms=int(self.config.get("poll_timeout_ms", DEFAULT_POLL_TIMEOUT_MS)),
        )
        cursor = self._state.load_cursor()
        self._state.set_polling(True)
        try:
            while self._running:
                snapshot = self._state.status_snapshot()
                if snapshot["paused"]:
                    await asyncio.sleep(min(5.0, max(1.0, snapshot["paused_until"] - time.time())))
                    continue

                try:
                    response = await client.get_updates(cursor=cursor)
                    next_cursor = response.get("get_updates_buf")
                    if isinstance(next_cursor, str):
                        cursor = next_cursor
                        self._state.save_cursor(cursor)
                    for message in response.get("msgs") or []:
                        await self._handle_inbound_message(message)
                    self._state.set_last_error(None)
                except WeixinSessionExpiredError:
                    logger.warning("[Weixin] session expired; pausing polling for one hour. %s", _default_relogin_hint())
                    self._state.pause_session()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._state.set_last_error(str(exc))
                    logger.exception("[Weixin] polling failed: %s", exc)
                    await asyncio.sleep(2)
        finally:
            self._state.set_polling(False)

    async def _handle_inbound_message(self, message: dict[str, Any]) -> None:
        if not is_supported_direct_text_message(message):
            return
        user_id = str(message.get("from_user_id"))
        text = extract_text_from_message(message).strip()
        if not text:
            return
        context_token = message.get("context_token")
        if isinstance(context_token, str) and context_token:
            self._state.set_context_token(user_id, context_token)
        self._state.set_last_inbound_at()
        inbound = InboundMessage(
            channel_name=self.name,
            chat_id=user_id,
            user_id=user_id,
            text=text,
            topic_id=user_id,
            metadata={
                "message_id": message.get("message_id"),
                "session_id": message.get("session_id"),
                "context_token": context_token,
            },
        )
        await self.bus.publish_inbound(inbound)


async def login_via_qr(
    *,
    base_url: str = DEFAULT_BASE_URL,
    state_dir: Path | None = None,
    timeout_ms: int = DEFAULT_LOGIN_TIMEOUT_MS,
    bot_type: str = DEFAULT_BOT_TYPE,
    print_qr: bool = True,
) -> WeixinAccount:
    store = WeixinStateStore(state_dir)
    client = WeixinApiClient(base_url=base_url, timeout_ms=DEFAULT_POLL_TIMEOUT_MS)
    refresh_count = 0

    while refresh_count < MAX_QR_REFRESH_COUNT:
        refresh_count += 1
        qr_payload = await client.get_bot_qrcode(bot_type=bot_type)
        qrcode = str(qr_payload.get("qrcode", "")).strip()
        qrcode_url = str(qr_payload.get("qrcode_img_content", "")).strip()
        if not qrcode or not qrcode_url:
            raise WeixinError("Weixin QR login failed: bridge did not return a QR code")
        if print_qr:
            print("QR URL:", qrcode_url)
            _print_ascii_qr(qrcode_url)
        deadline = time.time() + (timeout_ms / 1000)
        while time.time() < deadline:
            status = await client.get_qrcode_status(qrcode=qrcode)
            state = str(status.get("status", "wait"))
            if state == "confirmed" and status.get("bot_token"):
                account = WeixinAccount(
                    account_id=str(status.get("ilink_bot_id", "default")).strip() or "default",
                    token=str(status["bot_token"]).strip(),
                    base_url=str(status.get("baseurl", base_url)).strip() or base_url,
                    created_at=time.time(),
                    user_id=str(status.get("ilink_user_id")).strip() if status.get("ilink_user_id") else None,
                )
                store.save_account(account)
                return account
            if state == "expired":
                break
            if state == "scaned":
                print("QR scanned. Confirm in Weixin.")
            await asyncio.sleep(1)
    raise WeixinError("Weixin QR login timed out or QR code expired too many times")


def _print_ascii_qr(content: str) -> None:
    try:
        import qrcode

        qr = qrcode.QRCode(border=1)
        qr.add_data(content)
        qr.make(fit=True)
        matrix = qr.get_matrix()
        for row in matrix:
            print("".join("##" if cell else "  " for cell in row))
    except Exception:
        logger.info("[Weixin] qrcode package unavailable; printed URL only")


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_LOGIN_TIMEOUT_MS",
    "DEFAULT_POLL_TIMEOUT_MS",
    "SESSION_EXPIRED_ERRCODE",
    "WeixinAccount",
    "WeixinApiClient",
    "WeixinChannel",
    "WeixinError",
    "WeixinSessionExpiredError",
    "WeixinStateStore",
    "extract_text_from_message",
    "is_supported_direct_text_message",
    "login_via_qr",
]
