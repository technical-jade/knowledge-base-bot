"""Каталог статей рабочей копии: заголовки, разделы, ссылки на сайт."""

from dataclasses import dataclass
from pathlib import Path

import yaml

from .mdcheck import split_frontmatter


@dataclass(frozen=True)
class Article:
    path: str  # docs/devops/docker-volumes.md
    title: str
    section: str
    description: str = ""


def _meta(file: Path) -> dict:
    try:
        fm, _, _ = split_frontmatter(file.read_text(encoding="utf-8"))
        data = yaml.safe_load(fm) if fm else {}
        return data if isinstance(data, dict) else {}
    except (OSError, yaml.YAMLError):
        return {}


def url(site_url: str, path: str) -> str:
    rel = path.removeprefix("docs/")
    if rel == "index.md" or rel.endswith("/index.md"):
        rel = rel.removesuffix("index.md")
    else:
        rel = rel.removesuffix(".md") + ".html"
    return site_url + rel


def sections(repo: Path) -> dict[str, str]:
    """папка раздела → название, в порядке меню сайта (order, затем название)."""
    docs = repo / "docs"
    if not docs.is_dir():
        return {}
    found = []
    for d in docs.iterdir():
        if d.is_dir() and (d / "index.md").is_file():
            m = _meta(d / "index.md")
            try:
                order = float(m.get("order", 1000))
            except (TypeError, ValueError):
                order = 1000.0
            found.append((order, str(m.get("title") or d.name), d.name))
    return {key: title for _, title, key in sorted(found)}


def articles(repo: Path) -> list[Article]:
    """Статьи и учебники (учебник — его index.md; главы не перечисляются)."""
    docs = repo / "docs"
    out: list[Article] = []
    if not docs.is_dir():
        return out
    for f in sorted(docs.rglob("*.md")):
        rel = f.relative_to(repo).as_posix()
        parts = rel.split("/")
        if len(parts) < 3:
            continue  # docs/index.md
        section = parts[1]
        is_course = section == "courses" and len(parts) == 4
        if f.name == "index.md" and not is_course:
            continue  # страница раздела
        if section == "courses" and not (is_course and f.name == "index.md"):
            continue  # главы учебника
        m = _meta(f)
        out.append(Article(rel, str(m.get("title") or f.stem), section, str(m.get("description") or "")))
    return out


def course_files(repo: Path, index_path: str) -> list[str]:
    """Все файлы учебника по пути его index.md."""
    d = (repo / index_path).parent
    return sorted(p.relative_to(repo).as_posix() for p in d.glob("*.md"))
