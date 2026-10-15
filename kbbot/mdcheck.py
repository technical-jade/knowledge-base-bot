"""Проверка Markdown статей перед сборкой (ТЗ, раздел 6, шаг 4).

VitePress компилирует каждую страницу в Vue-компонент: интерполяция
`{{ … }}`, директивы и обработчики событий выполняются при сборке и в
браузере. Поэтому всё «исполняемое» вне блоков кода запрещено, а
компоненты пропускаются только из белого списка.
"""

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

BUILTIN_COMPONENTS = {"Badge", "OutboundLink"}
ARTICLE_KEYS = ("title", "description", "tags", "level", "created", "updated", "sources")
INDEX_KEYS = ("title", "description")
LEVELS = {"simple", "dev", "reference", "course"}

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_INLINE_CODE = re.compile(r"(`+)(?:(?!\1).)+?\1", re.S)
_TAG = re.compile(r"</?([A-Za-z][\w.-]*)((?:[^>\"']|\"[^\"]*\"|'[^']*')*)>", re.S)
_QUOTED = re.compile(r"\"[^\"]*\"|'[^']*'")
_ATTR_NAME = re.compile(r"([^\s=\"'/]+)(?:\s*=\s*\"\")?")

_FORBIDDEN = [
    (re.compile(r"<\s*script", re.I), "тег <script>"),
    (re.compile(r"<\s*style", re.I), "тег <style>"),
    (re.compile(r"<\s*iframe", re.I), "тег <iframe>"),
    (re.compile(r"javascript\s*:", re.I), "ссылка javascript:"),
]
# VitePress защищает от интерполяции только блоки ``` — inline-код и таблицы
# компилируются Vue. Поэтому {{ ищется везде, кроме блоков кода и ::: v-pre.
_MUSTACHE = re.compile(r"\{\{.*?\}\}", re.S)


@dataclass(frozen=True)
class Problem:
    path: str
    line: int
    text: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.text}" if self.line else f"{self.path}: {self.text}"


def component_names(components_dir: Path) -> set[str]:
    names = {p.stem for p in components_dir.glob("*.vue")} if components_dir.is_dir() else set()
    return names | BUILTIN_COMPONENTS


def _kebab(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "-", name).lower()


def split_frontmatter(text: str) -> tuple[str | None, str, int]:
    """(yaml или None, тело, номер строки, с которой начинается тело)."""
    if not text.startswith("---\n"):
        return None, text, 1
    end = text.find("\n---", 4)
    if end == -1:
        return None, text, 1
    after = text.find("\n", end + 4)
    body = "" if after == -1 else text[after + 1:]
    head_lines = text[: after + 1 if after != -1 else len(text)].count("\n")
    return text[4:end], body, head_lines + 1


_VPRE_OPEN = re.compile(r"^ {0,3}:::\s*v-pre\s*$")
_CONTAINER_OPEN = re.compile(r"^ {0,3}:::+\s*\S")
_CONTAINER_CLOSE = re.compile(r"^ {0,3}:::+\s*$")


def blank_code(body: str, inline: bool = True, vpre: bool = False) -> str:
    """Заменяет содержимое блоков кода пробелами (и inline-кода при inline=True).

    Переводы строк сохраняются, поэтому номера строк в ошибках совпадают
    с файлом. vpre=True — так же скрываются контейнеры `::: v-pre`.
    """
    out: list[str] = []
    fence: str | None = None
    vpre_depth = 0
    for line in body.split("\n"):
        m = _FENCE.match(line)
        if fence is not None:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not line.strip()[len(m.group(1)):].strip():
                fence = None
            out.append("")
            continue
        if m:
            fence = m.group(1)
            out.append("")
            continue
        if vpre:
            if vpre_depth == 0 and _VPRE_OPEN.match(line):
                vpre_depth = 1
                out.append("")
                continue
            if vpre_depth:
                if _CONTAINER_CLOSE.match(line):
                    vpre_depth -= 1
                elif _CONTAINER_OPEN.match(line):
                    vpre_depth += 1
                out.append("")
                continue
        out.append(line)
    text = "\n".join(out)
    if not inline:
        return text
    return _INLINE_CODE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


def _line_of(text: str, pos: int, base: int) -> int:
    return base + text.count("\n", 0, pos)


def check_text(text: str, rel: str, allowed: set[str]) -> list[Problem]:
    problems: list[Problem] = []
    fm_raw, body, base = split_frontmatter(text)
    is_index = Path(rel).name == "index.md"

    # Frontmatter
    if fm_raw is None:
        problems.append(Problem(rel, 1, "нет frontmatter (блок --- … --- в начале файла)"))
    else:
        try:
            fm = yaml.safe_load(fm_raw) or {}
        except yaml.YAMLError as e:
            problems.append(Problem(rel, 1, f"frontmatter не разбирается как YAML: {e}"))
            fm = None
        if fm is not None and not isinstance(fm, dict):
            problems.append(Problem(rel, 1, "frontmatter должен быть словарём"))
        elif fm is not None and fm.get("layout") != "home":
            for key in INDEX_KEYS if is_index else ARTICLE_KEYS:
                if key not in fm:
                    problems.append(Problem(rel, 1, f"во frontmatter нет поля `{key}`"))
            if not is_index:
                if "level" in fm and fm["level"] not in LEVELS:
                    problems.append(Problem(rel, 1, f"level должен быть одним из: {', '.join(sorted(LEVELS))}"))
                if "tags" in fm and not isinstance(fm["tags"], list):
                    problems.append(Problem(rel, 1, "tags должен быть списком"))
                if "sources" in fm and not isinstance(fm["sources"], (list, type(None))):
                    problems.append(Problem(rel, 1, "sources должен быть списком (может быть пустым)"))

    clean = blank_code(body)
    for m in _MUSTACHE.finditer(blank_code(body, inline=False, vpre=True)):
        problems.append(Problem(
            rel, _line_of(clean, m.start(), base),
            "запрещено: {{ … }} вне блока ``` (Vue выполнит это при сборке; оберните в блок кода или ::: v-pre)",
        ))

    for rx, what in _FORBIDDEN:
        for m in rx.finditer(clean):
            problems.append(Problem(rel, _line_of(clean, m.start(), base), f"запрещено: {what}"))

    allowed_all = allowed | {_kebab(n) for n in allowed}
    for m in _TAG.finditer(clean):
        name, attrs = m.group(1), m.group(2)
        line = _line_of(clean, m.start(), base)
        if (name[0].isupper() or "-" in name or "." in name) and name not in allowed_all:
            problems.append(Problem(rel, line, f"компонент <{name}> не из белого списка"))
        # Значения атрибутов не смотрим: внутри кавычек может быть любой текст
        for am in _ATTR_NAME.finditer(_QUOTED.sub('""', attrs)):
            attr = am.group(1)
            low = attr.lower()
            if low.startswith("on"):
                problems.append(Problem(rel, line, f"запрещён атрибут-обработчик {attr}= в <{name}>"))
            elif low.startswith(("v-", ":", "@")):
                problems.append(Problem(rel, line, f"запрещена Vue-директива {attr} в <{name}>"))

    return sorted(set(problems), key=lambda p: (p.path, p.line, p.text))


def check_files(repo: Path, rel_paths: list[str]) -> list[Problem]:
    """Проверяет изменённые .md внутри репозитория (удалённые пропускаются)."""
    allowed = component_names(repo / ".vitepress" / "theme" / "components")
    problems: list[Problem] = []
    for rel in rel_paths:
        p = repo / rel
        if not rel.endswith(".md") or not p.is_file():
            continue
        problems += check_text(p.read_text(encoding="utf-8"), rel, allowed)
    return problems


def main(argv: list[str] | None = None) -> int:
    """python -m kbbot.mdcheck <repo> [файлы…] — без файлов проверяет весь docs/."""
    import sys

    args = sys.argv[1:] if argv is None else argv
    if not args:
        print("usage: python -m kbbot.mdcheck <repo> [file.md ...]", file=sys.stderr)
        return 2
    repo = Path(args[0])
    files = args[1:] or [str(p.relative_to(repo)) for p in sorted((repo / "docs").rglob("*.md"))]
    problems = check_files(repo, files)
    for p in problems:
        print(p)
    print(f"проверено файлов: {len(files)}, проблем: {len(problems)}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
