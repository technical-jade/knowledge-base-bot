"""Экраны кнопочного управления: данные → текст + кнопки.

Чистые функции без Telegram и Claude: telegram.py и cli.py только
отображают Screen, Core подставляет данные. Коды кнопок (action) — короткие
ASCII-строки ≤ 64 байт (лимит callback_data): статьи — по номеру из
Store.ref, не по пути.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from .articles import Article, url
from .gitops import Commit

PAGE = 10
LABEL = 30
# Кнопки по две в ряд — только если обе не длиннее: иначе на телефоне текст обрезается
SHORT = 16

# Быстрые правки: код → (кнопка, инструкция для [edit])
PRESETS = {
    "short": ("✨ Короче", "Сократи статью: убери повторы и воду, сохрани суть, примеры и структуру."),
    "simple": ("🧒 Проще", "Перепиши проще: короче фразы, меньше жаргона, поясни термины."),
    "examples": ("➕ Примеры", "Добавь практические примеры там, где их не хватает."),
    "facts": ("🔄 Факты", "Проверь поиском актуальность фактов, версий и команд; исправь устаревшее и обнови источники."),
}

Ref = Callable[[str], int]

# Папка раздела попадает в callback_data как есть; кириллица или длинное имя
# (папку можно создать вручную) сломали бы всё сообщение — такие не кликаются
_SAFE_KEY = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


def safe_key(key: str) -> bool:
    return bool(_SAFE_KEY.match(key))


@dataclass(frozen=True)
class Btn:
    label: str
    action: str | None = None
    url: str | None = None


@dataclass
class Screen:
    md: str
    buttons: list[list[Btn]] = field(default_factory=list)


def short(title: str, n: int = LABEL) -> str:
    return title if len(title) <= n else title[: n - 1] + "…"


def plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        w = one
    elif 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        w = few
    else:
        w = many
    return f"{n} {w}"


def _articles_word(n: int) -> str:
    return plural(n, "статья", "статьи", "статей")


def pack(btns: list[Btn]) -> list[list[Btn]]:
    """Раскладка: каждая кнопка во всю ширину, две короткие подряд — в один ряд."""
    rows: list[list[Btn]] = []
    for b in btns:
        if rows and len(rows[-1]) == 1 and len(rows[-1][0].label) <= SHORT and len(b.label) <= SHORT:
            rows[-1].append(b)
        else:
            rows.append([b])
    return rows


def home(site: str, secs: dict[str, str], icons: dict[str, str], arts: list[Article]) -> Screen:
    count = {k: len([a for a in arts if a.section == k]) for k in secs}
    btns = [Btn(short(f"{icons.get(k, '📁')} {t} ({count[k]})", LABEL + 2), f"sec:{k}:0" if safe_key(k) else "noop")
            for k, t in secs.items()]
    md = f"📚 **База знаний** — {_articles_word(len(arts))}\n{site}"
    return Screen(md, pack(btns) + [[Btn("🕘 Недавние", "recent")]])


def section(site: str, key: str, secs: dict[str, str], icons: dict[str, str], arts: list[Article], page: int, ref: Ref) -> Screen:
    items = [a for a in arts if a.section == key]
    pages = max(1, -(-len(items) // PAGE))
    page = min(max(page, 0), pages - 1)
    count = plural(len(items), "учебник", "учебника", "учебников") if key == "courses" else _articles_word(len(items))
    head = f"{icons.get(key, '📁')} **{secs.get(key, key)}** — {count}"
    if pages > 1:
        head += f", стр. {page + 1}/{pages}"
    rows: list[list[Btn]] = []
    for a in items[page * PAGE : (page + 1) * PAGE]:
        r = ref(a.path)
        rows.append([Btn(short(f"📄 {a.title}"), f"card:{r}")])
        rows.append([Btn("✏️ Изменить", f"ed:{r}"), Btn("🗑 Удалить", f"del:{r}")])
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(Btn("◀️ Назад", f"sec:{key}:{page - 1}"))
        if page < pages - 1:
            nav.append(Btn("Дальше ▶️", f"sec:{key}:{page + 1}"))
        rows.append(nav)
    rows.append([Btn("⬅️ Разделы", "home"), Btn("➕ Новая", f"new:{key}")])
    md = head + ("\n\nПока пусто — добавьте статью кнопкой «➕ Новая»." if not items else "")
    return Screen(md, rows)


def is_course(a: Article) -> bool:
    return a.section == "courses"


def card(site: str, a: Article, secs: dict[str, str], icons: dict[str, str], ref: Ref, chapters: int | None) -> Screen:
    r = ref(a.path)
    sec = secs.get(a.section, a.section)
    lines = [f"📄 **{a.title}**"]
    if a.description:
        lines.append(a.description)
    meta = " · ".join(x for x in (f"обновлено {a.updated}" if a.updated else "", f"{icons.get(a.section, '📁')} {sec}") if x)
    lines.append(f"_{meta}_")
    if chapters is not None:
        lines.append(f"Глав: {chapters}")
    lines.append(url(site, a.path))
    first = [Btn("🌐 Открыть", url=url(site, a.path)), Btn("✏️ Править", f"ed:{r}")]
    if not is_course(a):
        first.append(Btn("🔀 Перенести", f"mv:{r}"))
    presets = [Btn(label, f"q:{code}:{r}") for code, (label, _) in PRESETS.items()]
    last = [Btn("🗑 Удалить", f"del:{r}"), Btn(short(f"⬅️ {sec}"), f"sec:{a.section}:0")]
    return Screen("\n".join(lines), pack(first) + pack(presets) + pack(last))


def move_targets(a: Article, secs: dict[str, str], icons: dict[str, str], ref: Ref) -> Screen:
    r = ref(a.path)
    btns = [Btn(short(f"{icons.get(k, '📁')} {t}"), f"mvto:{r}:{k}") for k, t in secs.items()
            if k not in (a.section, "courses") and safe_key(k)]
    md = f"🔀 Куда перенести «{a.title}»?" + ("" if btns else "\n\nДругих разделов нет.")
    return Screen(md, pack(btns) + [[Btn("⬅️ Назад", f"card:{r}")]])


def _article_of(file: str, by_path: dict[str, Article]) -> Article | None:
    if file in by_path:
        return by_path[file]
    parts = file.split("/")
    if len(parts) >= 4 and parts[:2] == ["docs", "courses"]:
        return by_path.get("/".join(parts[:3]) + "/index.md")
    return None


def recent(commits: list[Commit], arts: list[Article], ref: Ref, bot_email: str) -> Screen:
    by_path = {a.path: a for a in arts}
    lines = ["🕘 **Последние изменения**"]
    touched: list[Article] = []
    for c in commits:
        who = "🤖" if c.email == bot_email else "✍️"
        lines.append(f"• `{c.date}` {who} {c.subject}")
        for f in c.files:
            a = _article_of(f, by_path)
            if a and a not in touched:
                touched.append(a)
    if not commits:
        lines.append("Пока пусто.")
    rows = [[Btn(short(f"📄 {a.title}"), f"card:{ref(a.path)}")] for a in touched[:8]]
    if commits and commits[0].email == bot_email:
        rows.append([Btn("↩️ Отменить последнее", "undo")])
    rows.append([Btn("⬅️ Разделы", "home")])
    lines.append("\n🤖 — бот, ✍️ — вручную")
    return Screen("\n".join(lines), rows)


def result_buttons(site: str, path: str | None, ref: Ref, deleted: bool = False) -> list[list[Btn]]:
    """Кнопки под «Готово/Обновлено/Перенесено/Удалено»."""
    if deleted or not path:
        return [[Btn("↩️ Отменить", "undo"), Btn("📚 Список", "+home")]]
    r = ref(path)
    btns = [Btn("🌐 Открыть", url=url(site, path)), Btn("✏️ Править", f"ed:{r}")]
    if not path.startswith("docs/courses/"):
        btns.append(Btn("🔀 Перенести", f"+mv:{r}"))
    return pack(btns) + pack([Btn("↩️ Отменить", "undo"), Btn("🗑 Удалить", f"del:{r}")])


def article_buttons(arts: list[Article], ref: Ref) -> list[list[Btn]]:
    """Кнопки карточек под ответом /find (до 8); «+» — экран новым сообщением."""
    return [[Btn(short(f"📄 {a.title}"), f"+card:{ref(a.path)}")] for a in arts[:8]]
