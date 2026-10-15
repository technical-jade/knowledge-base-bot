"""Ядро бота без Telegram: команды, намерения, очередь, конвейер изменения.

Telegram (telegram.py) и kb-bot-cli (cli.py) — лишь разные Responder'ы
к одному и тому же Core, поэтому ночные проверки через CLI проверяют
тот же код, что работает в боте.
"""

import asyncio
import fcntl
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from . import articles as kb
from . import claude, mdcheck
from .builder import build, publish_release
from .config import Config
from .gitops import ConflictError, GitError, Repo
from .intent import Intent, ParseError, parse_intent, parse_write_result, safe_doc_path
from .store import Store

log = logging.getLogger("core")
TZ = ZoneInfo("Europe/Moscow")

STYLES = [
    ("simple", "Простыми словами"),
    ("dev", "Разработчику, с нуля в теме"),
    ("reference", "Справочник/шпаргалка"),
    ("course", "Учебник из глав"),
]
STYLE_NAMES = dict(STYLES)
FIX_ATTEMPTS = 2

AUTH_MSG = (
    "Claude не авторизован: обнови токен (`claude setup-token`) и запиши его на jade:\n\n"
    "```\nsudo -u kb-claude sh -c 'read -r t; printf \"%s\" \"$t\" > /etc/kb-claude/token'\n```"
)

HELP = """**kb-bot** — пишет и правит статьи базы знаний, отвечает на вопросы.

**Просто напишите**, что нужно:
• «Напиши гайд по Docker volumes» — новая статья (стиль — кнопкой)
• «В DevOps: статья про nginx» — в нужный раздел
• «Добавь пример с compose» — правка текущей статьи
• Ответ (Reply) на сообщение со ссылкой — правка этой статьи
• «Чем HTTP/2 отличается от HTTP/3?» — разговор с поиском в интернете
• «Сделай из этого статью» — статья из разговора
• «Что у меня есть про сети?» — поиск по базе

**Команды**
/new <тема> — новая статья
/ask <вопрос> — ответ по статьям базы
/find <запрос> — найти статьи
/list — разделы и статьи
/delete <что> — удалить статью (с подтверждением)
/undo — отменить последнее изменение бота
/deploy — опубликовать ручные правки из git
/reset — начать новый разговор
/help — эта справка

Сайт: {site}

⚠️ Секреты (пароли, токены, рабочие данные) в базу не класть."""


class Responder(Protocol):
    async def send(self, md: str, article: str | None = None) -> int | None: ...

    async def status(self, text: str) -> None: ...

    async def done(self) -> None: ...

    async def choose(self, question: str, options: list[tuple[str, str]], default: str, timeout: int) -> str: ...


class TaskFailed(Exception):
    """Задача остановлена; сообщение человеку уже в тексте исключения."""


@dataclass
class Outcome:
    primary: str | None
    section: str | None
    commit: str | None
    action: str
    note: str = ""


def today() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d")


class Core:
    def __init__(self, cfg: Config, store: Store | None = None) -> None:
        self.cfg = cfg
        self.store = store or Store(cfg.paths.state / "state.db")
        self.repo = Repo(cfg.paths.repo, cfg.git_name, cfg.git_email)
        self._write = asyncio.Lock()
        self._read = asyncio.Semaphore(2)
        self._chat = asyncio.Lock()

    # ---------- очередь ----------

    @asynccontextmanager
    async def write_slot(self, resp: Responder):
        """Записывающие задачи — строго по одной (и между ботом и kb-bot-cli)."""
        if self._write.locked():
            await resp.status("В очереди: жду завершения предыдущей задачи…")
        async with self._write:
            lock_path = self.cfg.paths.state / "write.lock"
            f = open(lock_path, "w")
            try:
                await asyncio.to_thread(fcntl.flock, f, fcntl.LOCK_EX)
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
                f.close()

    # ---------- вход ----------

    async def handle(self, text: str, resp: Responder, reply_to: int | None = None) -> None:
        text = text.strip()
        if not text:
            return
        if text.startswith("/"):
            cmd, _, arg = text.partition(" ")
            cmd = cmd.split("@", 1)[0].lower()
            arg = arg.strip()
            handlers = {
                "/start": self.cmd_help,
                "/help": self.cmd_help,
                "/reset": self.cmd_reset,
                "/list": self.cmd_list,
                "/deploy": self.cmd_deploy,
                "/undo": self.cmd_undo,
            }
            if cmd in handlers:
                return await self._run(cmd, resp, handlers[cmd](resp))
            if cmd == "/new":
                if not arg:
                    return await self._say(resp, "Напишите тему: `/new Docker volumes`")
                return await self._run("new", resp, self.do_new(resp, {"topic": arg, "section": None, "style": None}))
            if cmd == "/ask":
                if not arg:
                    return await self._say(resp, "Напишите вопрос: `/ask что я записывал о volumes?`")
                return await self._run("ask_kb", resp, self.do_read(resp, "ask_kb", arg))
            if cmd == "/find":
                if not arg:
                    return await self._say(resp, "Напишите запрос: `/find сети`")
                return await self._run("find", resp, self.do_read(resp, "find", arg))
            if cmd == "/delete":
                if not arg:
                    return await self._say(resp, "Что удалить? `/delete статья про nginx`")
                return await self._run("delete", resp, self.route_and_do(resp, f"Удали статью: {arg}", reply_to, force="delete"))
            return await self._say(resp, "Не знаю такой команды. /help — что я умею.")
        await self._run("route", resp, self.route_and_do(resp, text, reply_to))

    async def _say(self, resp: Responder, md: str, article: str | None = None) -> int | None:
        return await resp.send(md, article)

    async def _run(self, intent: str, resp: Responder, coro) -> None:
        task_id = self.store.task_start(intent)
        try:
            detail = await coro
            self.store.task_finish(task_id, "ok", str(detail or ""))
        except claude.ClaudeAuthError as e:
            log.error("claude auth", extra={"err": str(e)[:300]})
            self.store.task_finish(task_id, "auth", str(e))
            await self._say(resp, AUTH_MSG)
        except claude.ClaudeTimeout as e:
            self.store.task_finish(task_id, "timeout", str(e))
            await self._say(resp, f"⏱ {e}. Задача остановлена, изменения отменены.")
        except TaskFailed as e:
            self.store.task_finish(task_id, "failed", str(e))
            await self._say(resp, str(e))
        except Exception as e:  # noqa: BLE001 — человеку сообщаем любую ошибку
            log.exception("task crashed", extra={"intent": intent})
            self.store.task_finish(task_id, "error", repr(e))
            await self._say(resp, f"❌ Ошибка: `{type(e).__name__}: {str(e)[:300]}`")
        finally:
            await resp.done()
            self.store.prune()

    # ---------- маршрутизация ----------

    def _context(self, reply_article: str | None) -> str:
        items = kb.articles(self.cfg.paths.repo)
        listing = "\n".join(f"- {a.path}: {a.title}" for a in items) or "(пусто)"
        cur = self.store.current_article
        secs = ", ".join(f"{k} ({v})" for k, v in kb.sections(self.cfg.paths.repo).items())
        return (
            f"Текущая статья: {cur or 'нет'}\n"
            f"Сообщение — ответ (Reply) на сообщение бота о статье: {reply_article or 'нет'}\n"
            f"Разделы: {secs}\n"
            f"Статьи базы:\n{listing}"
        )

    async def route(self, text: str, reply_article: str | None) -> Intent:
        prompt = f"Контекст:\n{self._context(reply_article)}\n\nСообщение человека:\n{text}"
        last: Exception | None = None
        for _ in range(2):
            res = await claude.run(self.cfg.claude_cmd, "route", prompt, timeout=self.cfg.route_timeout)
            try:
                return parse_intent(res.text)
            except ParseError as e:
                last = e
                prompt += "\n\n(Ответь строго одним JSON-объектом.)"
        raise TaskFailed(f"Не понял запрос (ошибка разбора: {last}). Попробуйте командой — /help.")

    async def route_and_do(self, resp: Responder, text: str, reply_to: int | None, force: str | None = None):
        reply_article = self.store.message_article(reply_to) if reply_to else None
        await resp.status("Думаю…")
        it = await self.route(text, reply_article)
        if force and it.intent != force:
            if force == "delete" and it.intent in ("edit", "find", "ask_kb", "chat"):
                it = Intent("delete", {"target": it.args.get("target", "current")})
        log.info("intent", extra={"intent": it.intent, "args": {k: v for k, v in it.args.items() if k != "instruction"}})
        match it.intent:
            case "new":
                return await self.do_new(resp, it.args)
            case "edit":
                return await self.do_edit(resp, it.args, text, reply_article)
            case "ask_kb":
                return await self.do_read(resp, "ask_kb", text)
            case "find":
                return await self.do_read(resp, "find", text)
            case "chat":
                return await self.do_read(resp, "chat", text)
            case "help":
                return await self.do_read(resp, "help", text)
            case "to_article":
                return await self.do_to_article(resp, it.args, text)
            case "delete":
                return await self.do_delete(resp, it.args.get("target", "current"), reply_article)
            case "undo":
                return await self.cmd_undo(resp)
            case "clarify":
                await self._say(resp, it.args.get("question") or "Уточните, пожалуйста.")
                return "clarify"
        raise TaskFailed("Не понял запрос. /help — что я умею.")

    # ---------- чтение ----------

    async def do_read(self, resp: Responder, kind: str, text: str):
        # chat и ask_kb продолжают одну сессию разговора; find/help — разовые
        uses_chat = kind in ("chat", "ask_kb")
        async with self._read:
            async with self._chat if uses_chat else _null():
                await resp.status({"chat": "Думаю…", "ask_kb": "Ищу в базе…", "find": "Ищу статьи…", "help": "Думаю…"}[kind])
                resume = self.store.chat_session if uses_chat else None
                res = await claude.run(
                    self.cfg.claude_cmd, "read", f"[{kind}]\n{text}", resume=resume,
                    timeout=self.cfg.task_timeout, on_status=resp.status,
                )
                if uses_chat and res.session_id:
                    self.store.chat_session = res.session_id
        await self._say(resp, res.text or "(пустой ответ)")
        return kind

    # ---------- команды без Claude ----------

    async def cmd_help(self, resp: Responder):
        await self._say(resp, HELP.format(site=self.cfg.site_url))

    async def cmd_reset(self, resp: Responder):
        self.store.chat_session = None
        await self._say(resp, "Начинаем новый разговор.")

    async def cmd_list(self, resp: Responder):
        secs = kb.sections(self.cfg.paths.repo)
        items = kb.articles(self.cfg.paths.repo)
        lines = [f"**База знаний** — {self.cfg.site_url}"]
        for key, title in secs.items():
            arts = [a for a in items if a.section == key]
            lines.append(f"\n**{title}** ({len(arts)})")
            lines += [f"• [{a.title}]({kb.url(self.cfg.site_url, a.path)})" for a in arts] or ["• —"]
        await self._say(resp, "\n".join(lines))

    async def cmd_deploy(self, resp: Responder):
        async with self.write_slot(resp):
            await resp.status("Забираю изменения из git…")
            await self._pull()
            await asyncio.to_thread(self.repo.rollback)
            files = [p.relative_to(self.cfg.paths.repo).as_posix() for p in (self.cfg.paths.repo / "docs").rglob("*.md")]
            problems = mdcheck.check_files(self.cfg.paths.repo, files)
            if problems:
                raise TaskFailed("Проверка Markdown не прошла, сайт не обновлён:\n\n" + _problems_md(problems))
            await resp.status("Собираю сайт…")
            b = await build(self.cfg)
            if not b.ok:
                raise TaskFailed(f"Сборка упала, сайт не обновлён:\n\n```\n{b.log_tail[-2500:]}\n```")
            await resp.status("Публикую…")
            sha = self.repo.head()
            rel = await publish_release(self.cfg, sha)
        await self._say(resp, f"Опубликовано: коммит `{sha}` → {self.cfg.site_url}")
        return rel

    async def cmd_undo(self, resp: Responder):
        async with self.write_slot(resp):
            await self._pull()
            sha, email, subject = self.repo.last_commit()
            if email != self.cfg.git_email:
                raise TaskFailed(
                    f"Последний коммит в main — не от бота (`{sha}` {email}: «{subject}»). "
                    "Отменять чужие изменения не буду — откатите их через git."
                )
            choice = await resp.choose(
                f"Отменить последнее изменение бота?\n`{sha}` «{subject}»",
                [("yes", "Отменить"), ("no", "Не надо")], "no", 600,
            )
            if choice != "yes":
                await self._say(resp, "Хорошо, ничего не меняю.")
                return "cancelled"
            await resp.status("Отменяю…")
            await asyncio.to_thread(self.repo.revert_last, f"Отмена: {subject}")
            await self._build_or_fail(resp, rollback_to_origin=True)
            commit = await self._push_and_publish(resp)
        await self._say(resp, f"Отменено: «{subject}» (коммит `{commit}`). {self.cfg.site_url}")
        return commit

    # ---------- статьи ----------

    async def _ask_style(self, resp: Responder, topic: str) -> str:
        return await resp.choose(
            f"Как написать «{topic}»?", STYLES, "dev", self.cfg.style_timeout,
        )

    async def do_new(self, resp: Responder, args: dict):
        style = args.get("style") or await self._ask_style(resp, args["topic"])
        section = args.get("section")
        if style == "course":
            where = "учебник в docs/courses/<тема>/ (index.md + главы 01-…, 02-…)"
        elif section:
            where = f"раздел {section}"
        else:
            where = "раздел выбери сам по CLAUDE.md (или создай новый с index.md)"
        prompt = (
            f"[new]\nТема: {args['topic']}\nСтиль: {style} ({STYLE_NAMES.get(style, style)})\n"
            f"Куда: {where}\nСегодня: {today()}"
        )
        out = await self.change(resp, prompt, resume=None)
        return await self._report(resp, out)

    async def do_to_article(self, resp: Responder, args: dict, text: str):
        if not self.store.chat_session:
            raise TaskFailed("Разговора пока нет — не из чего делать статью. Напишите тему: «напиши статью про …».")
        style = args.get("style") or await self._ask_style(resp, "статья из разговора")
        section = args.get("section")
        where = "docs/courses/<тема>/" if style == "course" else (f"раздел {section}" if section else "раздел выбери сам")
        prompt = (
            f"[new]\nНапиши статью по нашему разговору выше (тема — то, что мы обсуждали).\n"
            f"Просьба человека: {text}\nСтиль: {style} ({STYLE_NAMES.get(style, style)})\nКуда: {where}\nСегодня: {today()}"
        )
        async with self._chat:
            out = await self.change(resp, prompt, resume=self.store.chat_session, chat=True)
        return await self._report(resp, out)

    def _resolve(self, target: str | None, reply_article: str | None) -> str | None:
        if reply_article:
            return reply_article
        if not target or target == "current":
            return self.store.current_article
        try:
            path = safe_doc_path(target)
        except ParseError:
            return None
        return path if (self.cfg.paths.repo / path).is_file() else None

    async def do_edit(self, resp: Responder, args: dict, text: str, reply_article: str | None):
        path = self._resolve(args.get("target"), reply_article)
        if not path:
            raise TaskFailed("Не понял, какую статью править. Ответьте (Reply) на сообщение со ссылкой или назовите статью.")
        if not (self.cfg.paths.repo / path).is_file():
            raise TaskFailed(f"Статьи `{path}` больше нет. /list — что есть.")
        resume = self.store.article_session(path)
        if args.get("move_to"):
            prompt = (
                f"[move]\nСтатья: {path}\nПеренести в раздел: {args['move_to']}\n"
                f"Просьба человека: {text}\nСегодня: {today()}"
            )
        else:
            prompt = (
                f"[edit]\nСтатья: {path}\nИнструкция: {args.get('instruction') or text}\n"
                f"Сообщение человека дословно: {text}\nСегодня: {today()}"
            )
        out = await self.change(resp, prompt, resume=resume, article=path)
        return await self._report(resp, out)

    async def do_delete(self, resp: Responder, target: str, reply_article: str | None):
        path = self._resolve(target, reply_article)
        if not path:
            raise TaskFailed("Не нашёл, какую статью удалить. Назовите её точнее или ответьте на сообщение со ссылкой.")
        title = next((a.title for a in kb.articles(self.cfg.paths.repo) if a.path == path), path)
        choice = await resp.choose(
            f"Удалить статью «{title}» (`{path}`)?", [("yes", "Удалить"), ("no", "Отмена")], "no", 600,
        )
        if choice != "yes":
            await self._say(resp, "Хорошо, ничего не удаляю.")
            return "cancelled"
        prompt = f"[delete]\nСтатья: {path}\nСегодня: {today()}"
        out = await self.change(resp, prompt, resume=None, article=path, expect_delete=path)
        return await self._report(resp, out)

    async def _report(self, resp: Responder, out: Outcome) -> str:
        secs = kb.sections(self.cfg.paths.repo)
        sec = secs.get(out.section or "", out.section or "")
        if out.action == "deleted":
            await self._say(resp, f"Удалено. {out.note}".strip() + f"\n{self.cfg.site_url}")
        else:
            link = kb.url(self.cfg.site_url, out.primary or "docs/index.md")
            verb = {"created": "Готово", "edited": "Обновлено", "moved": "Перенесено"}.get(out.action, "Готово")
            msg = f"{verb}: {link}" + (f" (раздел {sec})" if sec else "")
            if out.note:
                msg += f"\n\n{out.note}"
            await self._say(resp, msg, article=out.primary)
        return out.commit or ""

    # ---------- конвейер изменения (ТЗ, раздел 6) ----------

    async def _pull(self) -> None:
        try:
            await asyncio.to_thread(self.repo.pull_ff)
        except GitError as e:
            raise TaskFailed(f"`git pull` не прошёл — задачу не начинаю:\n```\n{str(e)[-800:]}\n```") from None

    async def _claude_write(self, resp: Responder, prompt: str, resume: str | None) -> claude.ClaudeResult:
        return await claude.run(
            self.cfg.claude_cmd, "write", prompt, resume=resume, timeout=self.cfg.task_timeout, on_status=resp.status,
        )

    async def _guard(self) -> None:
        outside = await asyncio.to_thread(self.repo.outside_docs)
        if outside:
            log.error("boundary violation", extra={"paths": outside})
            await asyncio.to_thread(self.repo.rollback)
            raise TaskFailed(f"⛔ Изменения вне docs/ ({', '.join(outside[:5])}) — всё отменено.")

    async def change(
        self,
        resp: Responder,
        prompt: str,
        resume: str | None,
        article: str | None = None,
        chat: bool = False,
        expect_delete: str | None = None,
    ) -> Outcome:
        async with self.write_slot(resp):
            await resp.status("Обновляю репозиторий…")
            await self._pull()
            await asyncio.to_thread(self.repo.rollback)
            try:
                return await self._change_locked(resp, prompt, resume, article, chat, expect_delete)
            except BaseException:
                # Любой сбой до коммита — рабочая копия возвращается к HEAD
                await asyncio.to_thread(self.repo.rollback)
                raise

    async def _change_locked(self, resp, prompt, resume, article, chat, expect_delete) -> Outcome:
        await resp.status("Пишу…")
        res = await self._claude_write(resp, prompt, resume)
        session = res.session_id or resume
        await self._guard()

        wr = None
        for attempt in range(FIX_ATTEMPTS + 1):
            try:
                wr = parse_write_result(res.text)
                break
            except ParseError as e:
                if attempt == FIX_ATTEMPTS:
                    raise TaskFailed(f"Claude не вернул корректный итоговый JSON ({e}) — изменения отменены.") from None
                res = await self._claude_write(
                    resp, f"[fix]\nИтоговый JSON не разобран: {e}. Верни корректный итоговый JSON.", session
                )
                session = res.session_id or session
                await self._guard()

        note = ""
        if wr.action == "moved":
            src, dst = wr.src, wr.dst
            if not (self.cfg.paths.repo / src).is_file():
                raise TaskFailed(f"Перенос: файла `{src}` нет — отменено.")
            if (self.cfg.paths.repo / dst).exists():
                raise TaskFailed(f"Перенос: `{dst}` уже существует — отменено.")
            await asyncio.to_thread(self.repo.mv, src, dst)
            await resp.status("Правлю ссылки…")
            res = await self._claude_write(
                resp,
                f"[links]\nФайл перенесён: {src} → {dst}. Поправь ссылки на него в других статьях "
                f"(и относительные ссылки внутри самой статьи, если они сломались). Верни итоговый JSON "
                f'с action "moved", from, to и primary = {dst}.',
                session,
            )
            session = res.session_id or session
            await self._guard()
            self.store.rename_article(src, dst)
            wr.primary = dst
            wr.section = dst.split("/")[1]
        elif wr.action == "deleted":
            targets = wr.files
            if expect_delete and expect_delete not in targets:
                targets = [expect_delete, *targets]
            for t in targets:
                if not (self.cfg.paths.repo / t).is_file():
                    raise TaskFailed(f"Удаление: файла `{t}` нет — отменено.")
            await asyncio.to_thread(self.repo.rm, targets)
            for t in targets:
                self.store.forget_article(t)
            note = "Удалены: " + ", ".join(f"`{t}`" for t in targets)

        changed = await asyncio.to_thread(self.repo.changed)
        if not changed:
            raise TaskFailed("Claude ничего не изменил.\n\n" + (res.text or "")[:1500])

        # Проверка Markdown и сборка; ошибки — Claude в той же сессии, до 2 попыток
        for attempt in range(FIX_ATTEMPTS + 1):
            changed = await asyncio.to_thread(self.repo.changed)
            problems = mdcheck.check_files(self.cfg.paths.repo, changed)
            if problems:
                if attempt == FIX_ATTEMPTS:
                    raise TaskFailed("Проверка Markdown не прошла — изменения отменены:\n\n" + _problems_md(problems))
                await resp.status("Исправляю ошибки проверки…")
                res = await self._claude_write(
                    resp, "[fix]\nПроверка Markdown нашла ошибки, исправь их:\n" + "\n".join(map(str, problems)), session
                )
                session = res.session_id or session
                await self._guard()
                continue
            await resp.status("Собираю сайт…")
            b = await build(self.cfg)
            if b.ok:
                break
            if attempt == FIX_ATTEMPTS:
                raise TaskFailed(f"Сборка не прошла — изменения отменены. Хвост лога:\n\n```\n{b.log_tail[-2500:]}\n```")
            await resp.status("Сборка упала, исправляю…")
            res = await self._claude_write(
                resp, f"[fix]\nСборка сайта упала. Хвост лога:\n```\n{b.log_tail[-3000:]}\n```\nИсправь статью.", session
            )
            session = res.session_id or session
            await self._guard()

        summary = wr.summary or f"Статья: {wr.primary}"
        await resp.status("Публикую…")
        try:
            await asyncio.to_thread(self.repo.commit, summary)
        except GitError as e:
            raise TaskFailed(f"Коммит не удался: {e}") from None
        commit = await self._push_and_publish(resp)

        if wr.action != "deleted" and wr.primary:
            self.store.current_article = wr.primary
            if session:
                self.store.bind_session(wr.primary, session)
        if chat and session:
            self.store.chat_session = session
        return Outcome(wr.primary, wr.section, commit, wr.action, note)

    async def _build_or_fail(self, resp: Responder, rollback_to_origin: bool = False) -> None:
        await resp.status("Собираю сайт…")
        b = await build(self.cfg)
        if not b.ok:
            if rollback_to_origin:
                await asyncio.to_thread(self.repo.reset_to_origin)
            raise TaskFailed(f"Сборка не прошла:\n\n```\n{b.log_tail[-2500:]}\n```")

    async def _push_and_publish(self, resp: Responder) -> str:
        try:
            foreign = await asyncio.to_thread(self.repo.sync_and_push, self.cfg.paths.conflicts)
        except ConflictError as e:
            raise TaskFailed(
                "⚠️ Конфликт с изменениями в GitHub — ничего не опубликовано, репозиторий возвращён к origin.\n"
                f"Конфликтуют: {', '.join(f'`{f}`' for f in e.files) or '?'}\n"
                f"Мои изменения сохранены патчем: `{e.patch}`"
            ) from None
        except GitError as e:
            await asyncio.to_thread(self.repo.reset_to_origin)
            raise TaskFailed(f"push не удался, изменения отменены:\n```\n{str(e)[-800:]}\n```") from None
        if foreign:
            # Пришли чужие коммиты — собранный сайт их не содержит
            b = await build(self.cfg)
            if not b.ok:
                raise TaskFailed(
                    "Изменения сохранены в git, но после слияния с чужими коммитами сборка упала — "
                    f"сайт не обновлён:\n```\n{b.log_tail[-2000:]}\n```"
                )
        commit = self.repo.head()
        await publish_release(self.cfg, commit)
        return commit


def _problems_md(problems) -> str:
    return "\n".join(f"• `{p}`" for p in problems[:20])


@asynccontextmanager
async def _null():
    yield
