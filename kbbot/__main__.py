"""Точка входа службы kb-bot: python -m kbbot."""

import asyncio
import logging
import sys

from aiogram import Bot
from aiogram.types import BotCommand, BotCommandScopeChat, MenuButtonCommands

from .config import ConfigError, load
from .core import Core
from .log import setup_logging
from .telegram import build_dispatcher

log = logging.getLogger("main")

# Меню «/» в Telegram — те же команды, что в /help
COMMANDS = [
    ("new", "Новая статья: /new <тема>"),
    ("ask", "Вопрос по базе: /ask <вопрос>"),
    ("find", "Найти статьи: /find <запрос>"),
    ("list", "Разделы и статьи (кнопки)"),
    ("recent", "Последние изменения"),
    ("delete", "Удалить статью или раздел (с подтверждением)"),
    ("undo", "Отменить последнее изменение бота"),
    ("deploy", "Опубликовать ручные правки из git"),
    ("reset", "Начать новый разговор"),
    ("help", "Что умеет бот"),
]


async def setup_menu(bot: Bot, chat_id: int) -> None:
    commands = [BotCommand(command=c, description=d) for c, d in COMMANDS]
    await bot.set_my_commands(commands)
    await bot.set_my_commands(commands, scope=BotCommandScopeChat(chat_id=chat_id))
    await bot.set_chat_menu_button(chat_id=chat_id, menu_button=MenuButtonCommands())


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
        await setup_menu(bot, cfg.allowed_id)
    except Exception:  # noqa: BLE001 — без меню бот всё равно работает
        log.exception("menu setup failed")
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
