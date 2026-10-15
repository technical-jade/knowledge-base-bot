"""Точка входа службы kb-bot: python -m kbbot."""

import asyncio
import logging
import sys

from aiogram import Bot

from .config import ConfigError, load
from .core import Core
from .log import setup_logging
from .telegram import build_dispatcher

log = logging.getLogger("main")


async def main() -> int:
    try:
        cfg = load()
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    setup_logging(cfg.bot_token)
    core = Core(cfg)
    bot = Bot(cfg.bot_token)
    dp = build_dispatcher(core, cfg.allowed_id)
    me = await bot.get_me()
    log.info("started", extra={"bot": me.username})
    try:
        await bot.send_message(cfg.allowed_id, "kb-bot запущен", disable_notification=True)
    except Exception:  # noqa: BLE001 — человек мог не нажать /start
        log.exception("startup message failed")
    try:
        await dp.start_polling(bot, handle_signals=True)
    finally:
        await bot.session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
