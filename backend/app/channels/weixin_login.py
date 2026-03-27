"""CLI entrypoint for DeerFlow native Weixin QR login."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import yaml

from deerflow.config.app_config import AppConfig

from app.channels.weixin import DEFAULT_BASE_URL, WeixinError, WeixinStateStore, login_via_qr


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Login DeerFlow Weixin channel via QR code")
    parser.add_argument("--base-url", default=None, help="Weixin bridge base URL")
    parser.add_argument("--state-dir", default=None, help="Override Weixin state directory")
    parser.add_argument("--timeout-ms", type=int, default=None, help="QR login timeout in milliseconds")
    parser.add_argument("--bot-type", default=None, help="Bridge bot type")
    parser.add_argument("--no-ascii", action="store_true", help="Disable ASCII QR rendering")
    return parser.parse_args()


async def _run() -> int:
    args = _parse_args()
    config_path = AppConfig.resolve_config_path()
    raw_config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    channels_config = raw_config.get("channels", {})
    weixin_config = channels_config.get("weixin", {}) if isinstance(channels_config, dict) else {}

    base_url = args.base_url or weixin_config.get("base_url") or DEFAULT_BASE_URL
    timeout_ms = args.timeout_ms or int(weixin_config.get("login_timeout_ms", 480000))
    bot_type = args.bot_type or str(weixin_config.get("bot_type", "3"))
    state_dir = Path(args.state_dir) if args.state_dir else None

    try:
        account = await login_via_qr(
            base_url=base_url,
            state_dir=state_dir,
            timeout_ms=timeout_ms,
            bot_type=bot_type,
            print_qr=not args.no_ascii,
        )
    except WeixinError as exc:
        print(f"Weixin login failed: {exc}")
        return 1

    store = WeixinStateStore(state_dir)
    print("Weixin login succeeded.")
    print(f"account_id: {account.account_id}")
    print(f"base_url:   {account.base_url}")
    print(f"state_dir:  {store.root}")
    if account.user_id:
        print(f"user_id:    {account.user_id}")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
