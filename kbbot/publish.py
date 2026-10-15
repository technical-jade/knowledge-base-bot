"""Публикация собранного сайта: релизы и атомарная замена ссылки (ТЗ, раздел 7).

/srv/kb/build-out/site  → /srv/kb/releases/<sha>  ← /srv/kb/site (симлинк)
"""

import os
import shutil
import time
from pathlib import Path

KEEP = 5


class PublishError(Exception):
    pass


def current(site_link: Path) -> Path | None:
    try:
        return Path(os.readlink(site_link))
    except OSError:
        return None


def switch(site_link: Path, target: Path) -> None:
    """ln -sfn во временное имя + rename: читатели видят либо старое, либо новое."""
    tmp = site_link.with_name(f".{site_link.name}.tmp")
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    tmp.symlink_to(target)
    os.replace(tmp, site_link)


def releases(releases_dir: Path) -> list[Path]:
    """Релизы от новых к старым."""
    if not releases_dir.is_dir():
        return []
    items = [p for p in releases_dir.iterdir() if p.is_dir() and not p.is_symlink()]
    return sorted(items, key=lambda p: p.stat().st_mtime, reverse=True)


def prune(releases_dir: Path, site_link: Path, keep: int = KEEP) -> list[Path]:
    live = current(site_link)
    removed = []
    for p in releases(releases_dir)[keep:]:
        if live is not None and p.resolve() == live.resolve():
            continue
        shutil.rmtree(p)
        removed.append(p)
    return removed


def publish(build_dir: Path, releases_dir: Path, site_link: Path, sha: str, keep: int = KEEP) -> Path:
    if not (build_dir / "index.html").is_file():
        raise PublishError(f"в {build_dir} нет index.html — сборка не завершилась")
    if not sha or not sha.isalnum():
        raise PublishError(f"некорректный sha: {sha!r}")
    releases_dir.mkdir(parents=True, exist_ok=True)
    target = releases_dir / sha
    if target.exists():
        # Пересборка того же коммита (например, /deploy без изменений)
        target = releases_dir / f"{sha}-{int(time.time())}"
    shutil.copytree(build_dir, target)
    os.utime(target)
    switch(site_link, target)
    prune(releases_dir, site_link, keep)
    return target


def rollback(releases_dir: Path, site_link: Path) -> Path:
    """Переключает сайт на релиз, предшествующий текущему."""
    items = releases(releases_dir)
    live = current(site_link)
    idx = next((i for i, p in enumerate(items) if live is not None and p.resolve() == live.resolve()), None)
    if idx is None or idx + 1 >= len(items):
        raise PublishError("нет предыдущего релиза для отката")
    target = items[idx + 1]
    switch(site_link, target)
    # Чтобы следующий откат шёл дальше в прошлое, а prune не удалил его первым
    os.utime(items[idx], (0, 0))
    os.utime(target)
    return target


def main(argv: list[str] | None = None) -> int:
    """python -m kbbot.publish status|rollback — ручное управление релизами."""
    import sys

    from .config import paths

    args = sys.argv[1:] if argv is None else argv
    p = paths()
    if args == ["status"]:
        live = current(p.site)
        for r in releases(p.releases):
            mark = "*" if live is not None and r.resolve() == live.resolve() else " "
            print(f"{mark} {r.name}  {time.strftime('%Y-%m-%d %H:%M', time.localtime(r.stat().st_mtime))}")
        return 0
    if args == ["rollback"]:
        print(f"сайт → {rollback(p.releases, p.site).name}")
        return 0
    print("usage: python -m kbbot.publish status|rollback", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
