import asyncio
from types import SimpleNamespace

from aiogram.exceptions import TelegramBadRequest

from kbbot import telegram as tg
from kbbot.store import Store


class FakeBot:
    def __init__(self, reject_html=False):
        self.sent, self.edits, self.deleted = [], [], []
        self.reject_html = reject_html
        self.n = 100

    async def send_message(self, chat_id, text, parse_mode=None, **kw):
        if self.reject_html and parse_mode == "HTML" and "⏳" not in text:
            raise TelegramBadRequest(method=SimpleNamespace(), message="can't parse entities")
        self.n += 1
        self.sent.append((text, parse_mode, kw.get("reply_markup")))
        return SimpleNamespace(message_id=self.n)

    async def edit_message_text(self, text, chat_id, message_id, parse_mode=None, **kw):
        self.edits.append((message_id, text))

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
