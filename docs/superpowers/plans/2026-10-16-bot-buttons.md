# Кнопочное управление kb-bot — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** /list с разделами и карточками статей, кнопки действий (✏️ 🗑 🔀 быстрые правки ↩️), ✖️ остановка задач, /recent, кнопки под /find.

**Architecture:** Экраны строит чистый модуль `kbbot/ui.py` (данные → `Screen(md, buttons)`); `Core.handle_action()` разбирает коды кнопок и переиспользует существующие `do_edit`/`do_delete`/`do_new`/`cmd_undo`; Responder (Telegram и CLI) получает кнопки, `edit`, `ask` (ForceReply) и `set_stop`. Короткие номера статей и ForceReply-подсказки — в SQLite (`refs`, `prompts`).

**Tech Stack:** Python 3.14, aiogram 3.31, SQLite, pytest; поддельный Claude `tests/fakes/fake_claude.py`.

**Spec:** `docs/superpowers/specs/2026-10-16-bot-buttons-design.md`

## Global Constraints

- `callback_data` ≤ 64 байт, ASCII; в кнопках номера `refs`, не пути.
- ≤ 10 статей на странице раздела; сетка разделов — по 2 в ряд.
- Навигация (`home`, `sec`, `card`, `recent`, `mv`) — без Claude, вне очереди, редактирует то же сообщение.
- Коммиты без `Co-Authored-By` (SPEC §2.7). Комментарии в коде — по-русски, только где нужно.
- Голосовые сообщения, reply-клавиатура, откат не последнего коммита, массовые операции — вне рамок.
- Существующие 99 тестов остаются зелёными.

## Review Focus

1. Нажатие кнопки статьи, удалённой/перенесённой после показа списка → «Статьи уже нет» и экран разделов, без исключения.
2. ✖️ во время работы Claude → процесс убит, рабочая копия чиста (`git status` пуст), задача `stopped`.
3. ✖️ в фазе коммита/публикации → отказ, публикация завершается.
4. Ответ на ForceReply-подсказку, начинающийся с `/`, — это команда, а не правка.
5. Длинные названия и много статей — в кнопке ≤ 30 символов, `callback_data` ≤ 64 байт, пагинация.

Тесты на каждый пункт — в задачах 2, 3, 4.

---

### Task 1: Store — refs и prompts; каталог — updated, иконки; git log

**Files:**
- Modify: `kbbot/store.py`, `kbbot/articles.py`, `kbbot/gitops.py`
- Test: `tests/test_store_ui.py` (новый)

**Produces:**
- `Store.ref(path: str) -> int`, `Store.ref_path(ref: int) -> str | None`
- `Store.bind_prompt(message_id: int, kind: str, arg: str)`, `Store.prompt(message_id: int) -> tuple[str, str] | None`
- `rename_article` переносит `refs.path`; `prune` чистит `prompts` как `messages`.
- `Article.updated: str` (строка даты или ""), `articles.icons(repo) -> dict[str, str]`, `articles.chapters(repo, index_path) -> int`
- `Repo.recent(n: int) -> list[Commit]`, `Commit(sha, date, email, subject, files: list[str])`

- [ ] Тесты: `ref` стабилен для одного пути и разный для разных; `ref_path` неизвестного → None; после `rename_article` старый номер ведёт на новый путь; prompt сохраняется/читается; `icons` — `icon` из frontmatter и умолчания; `updated` из frontmatter (yaml-дата → `YYYY-MM-DD`); `recent` на временном репозитории — порядок, файлы, email.
- [ ] Реализация, тесты зелёные, коммит.

### Task 2: `kbbot/ui.py` — экраны

**Files:** Create `kbbot/ui.py`; Test `tests/test_store_ui.py`

**Consumes:** Task 1. **Produces:**

```python
@dataclass(frozen=True)
class Btn: label: str; action: str | None = None; url: str | None = None
@dataclass
class Screen: md: str; buttons: list[list[Btn]]
PAGE = 10
DEFAULT_ICONS = {"devops": "⚙️", "frontend": "🎨", "backend": "🗄", "networking": "🌐", "tools": "🛠", "courses": "🎓", "misc": "📦"}
PRESETS = {"short": ("✨ Короче", "…"), "simple": ("🧒 Проще", "…"), "examples": ("➕ Примеры", "…"), "facts": ("🔄 Факты", "…")}
def home(site, secs, icons, arts) -> Screen
def section(site, key, secs, icons, arts, page, ref) -> Screen
def card(site, art, secs, icons, ref, chapters) -> Screen
def move_targets(art, secs, icons, ref) -> Screen
def recent(commits, arts_by_path, ref, bot_email, site) -> Screen
def result_buttons(site, path, ref, deleted=False) -> list[list[Btn]]
def article_buttons(paths_titles, ref) -> list[list[Btn]]   # под /find
def short(title, n=30) -> str
```

- [ ] Тесты: home — счётчики и сетка по 2, последний ряд «🕘 Недавние»; section — 23 статьи → 3 страницы, кнопки ◀️/▶️ только где нужны, пустой раздел «Пока пусто»; card статьи — 🌐 url, все действия; card учебника — без 🔀, «глав: N»; move_targets без текущего раздела и courses; recent — ↩️ только если последний коммит от бота; длинное название обрезано до 30 с «…»; **все `action` во всех экранах ≤ 64 байт ASCII** (обход всех кнопок).
- [ ] Реализация, тесты, коммит.

### Task 3: Core — действия, ForceReply, результат с кнопками, /recent, /find

**Files:** Modify `kbbot/core.py`; Test `tests/test_core.py` (Resp получает новые методы), `tests/test_actions.py` (новый)

**Consumes:** Task 1–2. **Produces:**
- Responder: `send(md, article=None, buttons=None)`, `edit(message_id: int | None, md, buttons=None)`, `ask(md) -> int | None`, `set_stop(action: str | None)`.
- `Core.handle_action(action: str, resp, message_id: int | None) -> None`.
- `Core.handle` — сначала ответ на prompt (если текст не команда).
- `cmd_list` → `ui.home`; `/recent` → `ui.recent`; `_report` — кнопки результата; `do_read(find/ask_kb)` — кнопки статей из ответа.

- [ ] Тесты: `home`/`sec:devops:0`/`card:N` вызывают `edit` с нужным экраном и без Claude (calls() пуст); устаревший ref → edit с «Статьи уже нет» и разделами; `ed:N` → `ask`, затем `handle(текст, reply_to=id)` → Claude [edit] без route (в calls() нет mode route), prompt содержит путь; `/команда` в ответ на подсказку — команда; `new:devops` → ask → ответ → [new] с «раздел devops»; `q:short:N` → [edit] с текстом пресета; `mvto:N:misc` → перенос; `del:N` → подтверждение и удаление; `undo` → cmd_undo; результат «Готово» несёт кнопки (↩️ `undo`); /find с путём в ответе → кнопка карточки; `/recent` — экран.
- [ ] Реализация, тесты, коммит.

### Task 4: Остановка задач

**Files:** Modify `kbbot/core.py`, `kbbot/claude.py`; Test `tests/test_actions.py`

**Produces:** `Core._running: dict[int, asyncio.Task]`, `Core._critical: set[int]`, `Core.stop(task_id) -> str`; `_run` ставит `resp.set_stop(f"stop:{id}")`; `claude.run` при отмене убивает процесс.

- [ ] Тесты: поддельный Claude со сценарием `sleep` (новое поле в fake_claude: пауза перед результатом) → во время работы `handle_action("stop:<id>")` → ответ «Остановлено», `git status --porcelain` пуст, журнал `stopped`, процесс fake_claude завершён; `stop` для несуществующей/завершённой — «Задача уже завершена»; в критической фазе (`_critical`) — отказ «Уже публикуется».
- [ ] Реализация, тесты, коммит.

### Task 5: Telegram и CLI

**Files:** Modify `kbbot/telegram.py`, `kbbot/cli.py`, `kbbot/__main__.py`; Test `tests/test_telegram.py`

- [ ] TgResponder: `send(..., buttons)` — клавиатура на последнем куске; `edit` — `edit_message_text` с клавиатурой, «not modified» игнорируется, иное → `send`; `ask` — `ForceReply(input_field_placeholder=…)`; `set_stop` — кнопка ✖️ на статусе (и снятие).
- [ ] Диспетчер: callback не `ch:` → `cb.answer()` сразу, затем `core.handle_action(data, resp, cb.message.message_id if доступно)`.
- [ ] CLI: печать кнопок `[метка код]`, `--press <код> [--msg id]`; `ask` печатает и возвращает id.
- [ ] `/recent` в меню (`COMMANDS`).
- [ ] Тесты: клавиатура в send/edit, callback_data совпадают с action, ForceReply в ask, кнопка stop в статусе.
- [ ] Коммит.

### Task 6: Тексты, установка, живая проверка, документация

- [ ] HELP, `prompts/about.md`, README — /list с кнопками, /recent, ✖️.
- [ ] Полный pytest; `sudo deploy/install.sh` (перезапускает kb-bot); `getMyCommands` содержит `recent`.
- [ ] Живьём через `kb-bot-cli`: `--press home`, `sec:devops:0`, `card:N`; `q:short` на тестовой статье → затем `undo`; `stop` во время задачи; `/recent`.
- [ ] REPORT.md — дополнение; коммит и push; финальное ревью свежим агентом.
