import asyncio
from types import SimpleNamespace

from aiogram.exceptions import TelegramBadRequest

from kbbot import telegram as tg
from kbbot.store import Store


class FakeBot:
    def __init__(self, reject_html=False, edit_error=None):
        self.sent, self.edits, self.deleted, self.markups = [], [], [], []
        self.reject_html = reject_html
        self.edit_error = edit_error
        self.n = 100

    async def send_message(self, chat_id, text, parse_mode=None, **kw):
        if self.reject_html and parse_mode == "HTML" and "⏳" not in text:
            raise TelegramBadRequest(method=SimpleNamespace(), message="can't parse entities")
        self.n += 1
        self.sent.append((text, parse_mode, kw.get("reply_markup")))
        return SimpleNamespace(message_id=self.n)

    async def edit_message_text(self, text, chat_id, message_id, parse_mode=None, **kw):
        if self.edit_error:
            raise TelegramBadRequest(method=SimpleNamespace(), message=self.edit_error)
        self.edits.append((message_id, text))
        self.markups.append(kw.get("reply_markup"))

    async def delete_message(self, chat_id, message_id):
        self.deleted.append(message_id)


def test_send_binds_article_and_splits(tmp_path):
    bot, store = FakeBot(), Store(tmp_path / "s.db")
    r = tg.TgResponder(bot, 1, store)
    last = asyncio.run(r.send("**слово** текст " * 1000, article="docs/a/b.md"))
    assert len(bot.sent) > 1 and all(len(t) <= 4096 for t, *_ in bot.sent)
    assert store.message_article(last) == "docs/a/b.md"


def test_html_rejected_falls_back_to_plain(tmp_path):
    bot = FakeBot(reject_html=True)
    r = tg.TgResponder(bot, 1, Store(tmp_path / "s.db"))
    asyncio.run(r.send("**жирный** <тег>"))
    assert bot.sent[-1][1] is None and "**жирный**" in bot.sent[-1][0]


def test_status_is_one_message_throttled(tmp_path, monkeypatch):
    monkeypatch.setattr(tg, "STATUS_EVERY", 0.2)
    bot = FakeBot()
    r = tg.TgResponder(bot, 1, Store(tmp_path / "s.db"))

    async def go():
        for i in range(10):
            await r.status(f"шаг {i}")
        await asyncio.sleep(0.4)
        await r.done()

    asyncio.run(go())
    assert len(bot.sent) == 1  # одно сообщение статуса
    assert len(bot.edits) <= 2 and bot.edits[-1][1].endswith("шаг 9")
    assert bot.deleted == [bot.n]


def test_choose_by_button_and_timeout(tmp_path):
    bot = FakeBot()
    r = tg.TgResponder(bot, 1, Store(tmp_path / "s.db"))

    async def press():
        while not tg._choices:
            await asyncio.sleep(0.01)
        token = next(iter(tg._choices))
        tg._choices[token].set_result("reference")

    async def go():
        res, _ = await asyncio.gather(r.choose("Как?", [("dev", "Разработчику"), ("reference", "Шпаргалка")], "dev", 5), press())
        return res

    assert asyncio.run(go()) == "reference"
    assert "Выбрано: Шпаргалка" in bot.edits[-1][1]
    assert asyncio.run(r.choose("Как?", [("dev", "Разработчику")], "dev", 0.05)) == "dev"
    assert "Время вышло" in bot.edits[-1][1]


# ---------- кнопки ----------

from aiogram.types import ForceReply, InlineKeyboardMarkup  # noqa: E402

from kbbot.ui import Btn  # noqa: E402

BTNS = [[Btn("🌐", url="https://x/kb/a.html"), Btn("✏️", "ed:1")], [Btn("↩️", "undo")]]


def kb_data(markup):
    return [(b.text, b.callback_data, b.url) for row in markup.inline_keyboard for b in row]


def test_send_with_buttons_on_last_chunk(tmp_path):
    bot = FakeBot()
    r = tg.TgResponder(bot, 1, Store(tmp_path / "s.db"))
    asyncio.run(r.send("слово " * 1500, buttons=BTNS))
    assert len(bot.sent) > 1 and bot.sent[0][2] is None
    assert kb_data(bot.sent[-1][2]) == [("🌐", None, "https://x/kb/a.html"), ("✏️", "ed:1", None), ("↩️", "undo", None)]


def test_edit_in_place_and_fallbacks(tmp_path):
    bot = FakeBot()
    r = tg.TgResponder(bot, 1, Store(tmp_path / "s.db"))
    asyncio.run(r.edit(7, "**экран**", BTNS))
    assert bot.edits[-1] == (7, "<b>экран</b>") and kb_data(bot.markups[-1])[1][1] == "ed:1"
    asyncio.run(r.edit(None, "новое", BTNS))
    assert bot.sent[-1][0] == "новое" and isinstance(bot.sent[-1][2], InlineKeyboardMarkup)
    bot2 = FakeBot(edit_error="Bad Request: message is not modified")
    asyncio.run(tg.TgResponder(bot2, 1, Store(tmp_path / "s2.db")).edit(7, "то же", BTNS))
    assert bot2.sent == []
    bot3 = FakeBot(edit_error="Bad Request: message to edit not found")
    asyncio.run(tg.TgResponder(bot3, 1, Store(tmp_path / "s3.db")).edit(7, "старое", BTNS))
    assert bot3.sent[-1][0] == "старое"


def test_ask_uses_force_reply(tmp_path):
    bot = FakeBot()
    mid = asyncio.run(tg.TgResponder(bot, 1, Store(tmp_path / "s.db")).ask("Что изменить?"))
    assert mid == bot.n and isinstance(bot.sent[-1][2], ForceReply)


def test_status_carries_stop_button_until_cleared(tmp_path):
    bot = FakeBot()
    r = tg.TgResponder(bot, 1, Store(tmp_path / "s.db"))

    async def go():
        await r.set_stop("stop:3")
        await r.status("Пишу…")
        await r.set_stop(None)

    asyncio.run(go())
    assert kb_data(bot.sent[0][2]) == [("✖️ Остановить", "stop:3", None)]
    assert bot.edits[-1][0] == 101 and bot.markups[-1] is None  # кнопка снята с того же статуса


def test_callback_dispatch(tmp_path):
    calls = []

    class FakeCore:
        store = Store(tmp_path / "s.db")

        async def handle_action(self, action, resp, mid):
            calls.append((action, mid, resp.chat_id))

    answered = []

    async def answer(text=None):
        answered.append(text)

    cb = SimpleNamespace(data="card:5", message=SimpleNamespace(message_id=42, chat=SimpleNamespace(id=1)),
                         from_user=SimpleNamespace(id=1), answer=answer)
    asyncio.run(tg.on_action(FakeCore(), FakeBot(), cb))
    assert calls == [("card:5", 42, 1)] and answered == [None]
    cb.message = None  # сообщение слишком старое — недоступно
    asyncio.run(tg.on_action(FakeCore(), FakeBot(), cb))
    assert calls[-1] == ("card:5", None, 1)
