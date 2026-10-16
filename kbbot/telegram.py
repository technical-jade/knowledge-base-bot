"""Telegram-слой: aiogram 3, один разрешённый пользователь, Responder для Core."""

import asyncio
import logging
import secrets
import time

from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import CallbackQuery, ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions, Message

from .access import AccessMiddleware
from .core import Core
from .store import Store
from .tgformat import esc, plain_chunks, to_messages
from .ui import Btn

log = logging.getLogger("telegram")

STATUS_EVERY = 3.0
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)

# Ожидающие выбора кнопки: токен → future
_choices: dict[str, asyncio.Future] = {}


def markup(buttons: list[list[Btn]] | None) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=b.label, url=b.url) if b.url else InlineKeyboardButton(text=b.label, callback_data=b.action)
         for b in row]
        for row in buttons
    ])


class TgResponder:
    def __init__(self, bot: Bot, chat_id: int, store: Store) -> None:
        self.bot = bot
        self.chat_id = chat_id
        self.store = store
        self._status_id: int | None = None
        self._status_text = ""
        self._status_sent = 0.0
        self._pending: asyncio.Task | None = None
        self._stop: str | None = None

    async def send(self, md: str, article: str | None = None, buttons: list[list[Btn]] | None = None) -> int | None:
        await self._drop_status()
        last = None
        chunks = to_messages(md)
        for i, chunk in enumerate(chunks):
            kb = markup(buttons) if i == len(chunks) - 1 else None
            try:
                msg = await self.bot.send_message(
                    self.chat_id, chunk or "…", parse_mode="HTML", link_preview_options=NO_PREVIEW, reply_markup=kb
                )
            except TelegramBadRequest as e:
                # Разметка не принята — повтор простым текстом
                log.warning("html rejected", extra={"err": str(e)[:200]})
                msg = None
                parts = plain_chunks(md)
                for j, part in enumerate(parts):
                    msg = await self.bot.send_message(
                        self.chat_id, part or "…", link_preview_options=NO_PREVIEW,
                        reply_markup=markup(buttons) if j == len(parts) - 1 else None,
                    )
                    if article:
                        self.store.bind_message(msg.message_id, article)
                last = msg.message_id if msg else last
                break
            last = msg.message_id
            if article:
                self.store.bind_message(msg.message_id, article)
        return last

    async def edit(self, message_id: int | None, md: str, buttons: list[list[Btn]] | None = None) -> None:
        """Сменить экран на месте; не вышло (сообщение старое/удалено) — новым сообщением."""
        if message_id is not None:
            try:
                await self.bot.edit_message_text(
                    to_messages(md)[0], chat_id=self.chat_id, message_id=message_id, parse_mode="HTML",
                    link_preview_options=NO_PREVIEW, reply_markup=markup(buttons),
                )
                return
            except TelegramBadRequest as e:
                if "not modified" in str(e):
                    return
                log.info("edit failed, sending new", extra={"err": str(e)[:200]})
        await self.send(md, buttons=buttons)

    async def ask(self, md: str) -> int | None:
        await self._drop_status()
        msg = await self.bot.send_message(
            self.chat_id, to_messages(md)[0], parse_mode="HTML",
            reply_markup=ForceReply(force_reply=True, input_field_placeholder="Ответ…"),
        )
        return msg.message_id

    def _stop_markup(self) -> InlineKeyboardMarkup | None:
        return markup([[Btn("✖️ Остановить", self._stop)]]) if self._stop else None

    async def set_stop(self, action: str | None) -> None:
        changed = action != self._stop
        self._stop = action
        if changed and self._status_id is not None:
            await self._edit_status()

    async def status(self, text: str) -> None:
        self._status_text = text
        if self._status_id is None:
            try:
                m = await self.bot.send_message(
                    self.chat_id, f"⏳ {esc(text)}", parse_mode="HTML", disable_notification=True,
                    reply_markup=self._stop_markup(),
                )
                self._status_id = m.message_id
                self._status_sent = time.monotonic()
            except Exception:  # noqa: BLE001 — статус не критичен
                log.exception("status send")
            return
        wait = STATUS_EVERY - (time.monotonic() - self._status_sent)
        if wait <= 0 and not self._pending:
            await self._edit_status()
        elif not self._pending:
            self._pending = asyncio.create_task(self._delayed(max(wait, 0)))

    async def _delayed(self, wait: float) -> None:
        await asyncio.sleep(wait)
        self._pending = None
        await self._edit_status()

    async def _edit_status(self) -> None:
        if self._status_id is None:
            return
        try:
            await self.bot.edit_message_text(
                f"⏳ {esc(self._status_text)}", chat_id=self.chat_id, message_id=self._status_id, parse_mode="HTML",
                reply_markup=self._stop_markup(),
            )
        except TelegramRetryAfter as e:
            self._status_sent = time.monotonic() + e.retry_after
            return
        except TelegramBadRequest:
            pass  # «message is not modified»
        self._status_sent = time.monotonic()

    async def _drop_status(self) -> None:
        if self._pending:
            self._pending.cancel()
            self._pending = None
        if self._status_id is not None:
            try:
                await self.bot.delete_message(self.chat_id, self._status_id)
            except TelegramBadRequest:
                pass
            self._status_id = None

    async def done(self) -> None:
        await self._drop_status()

    async def choose(self, question: str, options: list[tuple[str, str]], default: str, timeout: int) -> str:
        await self._drop_status()
        token = secrets.token_hex(4)
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=f"ch:{token}:{key}")] for key, label in options]
        )
        labels = dict(options)
        msg = await self.bot.send_message(self.chat_id, to_messages(question)[0], parse_mode="HTML", reply_markup=kb)
        fut = asyncio.get_running_loop().create_future()
        _choices[token] = fut
        try:
            key = await asyncio.wait_for(fut, timeout)
            note = f"Выбрано: {labels.get(key, key)}"
        except TimeoutError:
            key = default
            note = f"Время вышло — {labels.get(key, key)}"
        finally:
            _choices.pop(token, None)
        try:
            await self.bot.edit_message_text(
                to_messages(question)[0] + f"\n\n<i>{esc(note)}</i>", chat_id=self.chat_id, message_id=msg.message_id, parse_mode="HTML"
            )
        except TelegramBadRequest:
            pass
        return key


async def on_action(core: Core, bot: Bot, cb: CallbackQuery) -> None:
    """Нажатие кнопки экрана/действия. Ответ на callback — сразу (иначе часики в клиенте)."""
    await cb.answer()
    msg = cb.message
    chat_id = msg.chat.id if msg else cb.from_user.id
    resp = TgResponder(bot, chat_id, core.store)
    await core.handle_action(cb.data or "", resp, msg.message_id if msg else None)


def build_dispatcher(core: Core, allowed_id: int) -> Dispatcher:
    dp = Dispatcher()
    dp.update.outer_middleware(AccessMiddleware(allowed_id))

    @dp.callback_query(F.data.startswith("ch:"))
    async def on_choice(cb: CallbackQuery) -> None:
        _, token, key = (cb.data or "").split(":", 2)
        fut = _choices.get(token)
        if fut and not fut.done():
            fut.set_result(key)
            await cb.answer()
        else:
            await cb.answer("Этот выбор уже не актуален")

    @dp.callback_query()
    async def on_button(cb: CallbackQuery, bot: Bot) -> None:
        await on_action(core, bot, cb)

    @dp.message(F.text)
    async def on_text(msg: Message, bot: Bot) -> None:
        resp = TgResponder(bot, msg.chat.id, core.store)
        reply_to = msg.reply_to_message.message_id if msg.reply_to_message else None
        await core.handle(msg.text or "", resp, reply_to)

    @dp.message()
    async def on_other(msg: Message) -> None:
        await msg.answer("Я понимаю только текст. /help — что я умею.")

    return dp
