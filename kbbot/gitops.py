"""Git-операции над рабочей копией /srv/kb/repo (ТЗ, раздел 6).

Синхронные: вызываются из asyncio.to_thread. Всё, что меняет репозиторий,
выполняется только под общим замком записи.
"""

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


class GitError(Exception):
    pass


class ConflictError(GitError):
    def __init__(self, files: list[str], patch: Path) -> None:
        super().__init__(f"конфликт: {', '.join(files) or '?'}")
        self.files = files
        self.patch = patch


@dataclass
class Repo:
    path: Path
    name: str = "kb-bot"
    email: str = "kb-bot@jade"
    branch: str = "main"

    def git(self, *args: str, check: bool = True, timeout: int = 120) -> str:
        env = {
            **os.environ,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_NAME": self.name,
            "GIT_AUTHOR_EMAIL": self.email,
            "GIT_COMMITTER_NAME": self.name,
            "GIT_COMMITTER_EMAIL": self.email,
            "LC_ALL": "C.UTF-8",
        }
        p = subprocess.run(
            ["git", "-C", str(self.path), *args], capture_output=True, text=True, env=env, timeout=timeout
        )
        if check and p.returncode != 0:
            raise GitError(f"git {' '.join(args)}: {(p.stderr or p.stdout).strip()[-800:]}")
        return p.stdout

    # чтение
    def head(self, short: bool = True) -> str:
        return self.git("rev-parse", *(["--short"] if short else []), "HEAD").strip()

    def changed(self) -> list[str]:
        """Изменённые/новые/удалённые пути (включая неотслеживаемые файлы)."""
        out = self.git("status", "--porcelain=v1", "-z", "--untracked-files=all")
        paths: list[str] = []
        items = out.split("\0")
        i = 0
        while i < len(items):
            entry = items[i]
            if not entry:
                i += 1
                continue
            status, path = entry[:2], entry[3:]
            paths.append(path)
            if "R" in status or "C" in status:
                # формат -z: после переименования идёт исходный путь
                i += 1
                if i < len(items) and items[i]:
                    paths.append(items[i])
            i += 1
        return paths

    def outside_docs(self) -> list[str]:
        return [p for p in self.changed() if not p.startswith("docs/")]

    def last_commit(self) -> tuple[str, str, str]:
        """(sha, email автора, тема)."""
        sha, email, subject = self.git("log", "-1", "--format=%h%x00%ae%x00%s").rstrip("\n").split("\0")
        return sha, email, subject

    # изменения
    def pull_ff(self) -> None:
        self.git("pull", "--ff-only", "-q", "origin", self.branch, timeout=180)

    def rollback(self) -> None:
        """Выбросить все незакоммиченные изменения (охрана границ, откат задачи)."""
        self.git("reset", "-q", "--hard", "HEAD")
        # Неотслеживаемое — везде (Claude физически пишет только в docs/, но
        # охрана границ не должна на это полагаться); игнорируемое git не трогается
        self.git("clean", "-fdq")

    def mv(self, src: str, dst: str) -> None:
        (self.path / dst).parent.mkdir(parents=True, exist_ok=True)
        self.git("mv", "--", src, dst)

    def rm(self, paths: list[str]) -> None:
        tracked = [p for p in paths if self.git("ls-files", "--", p).strip()]
        if tracked:
            self.git("rm", "-q", "-r", "-f", "--", *tracked)
        for p in paths:
            f = self.path / p
            if f.is_file():
                f.unlink()

    def commit(self, message: str) -> str:
        self.git("add", "-A")
        staged = [p for p in self.git("diff", "--cached", "--name-only").split("\n") if p]
        if not staged:
            raise GitError("нет изменений для коммита")
        # Вторая линия охраны границ: в коммит попадает только docs/
        if outside := [p for p in staged if not p.startswith("docs/")]:
            self.git("reset", "-q")
            raise GitError(f"изменения вне docs/: {', '.join(outside)}")
        self.git("commit", "-q", "-m", message)
        return self.head()

    def sync_and_push(self, conflicts_dir: Path) -> bool:
        """pull --rebase + push. True — пришли чужие коммиты (нужна пересборка).

        Конфликт: rebase отменяется, наш коммит сохраняется патчем, ветка
        возвращается к origin — молча ничего не разрешаем.
        """
        before_remote = self.git("rev-parse", f"origin/{self.branch}").strip()
        self.git("fetch", "-q", "origin", self.branch, timeout=180)
        after_remote = self.git("rev-parse", f"origin/{self.branch}").strip()
        rc = subprocess.run(
            ["git", "-C", str(self.path), "rebase", "-q", f"origin/{self.branch}"],
            capture_output=True, text=True,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_COMMITTER_NAME": self.name, "GIT_COMMITTER_EMAIL": self.email},
        ).returncode
        if rc != 0:
            files = [f for f in self.git("diff", "--name-only", "--diff-filter=U", check=False).split("\n") if f]
            self.git("rebase", "--abort", check=False)
            conflicts_dir.mkdir(parents=True, exist_ok=True)
            patch = conflicts_dir / f"{time.strftime('%Y%m%d-%H%M%S')}.patch"
            patch.write_text(self.git("format-patch", "-1", "--stdout", "HEAD"))
            self.git("reset", "-q", "--hard", f"origin/{self.branch}")
            raise ConflictError(files, patch)
        self.git("push", "-q", "origin", f"HEAD:{self.branch}", timeout=180)
        return before_remote != after_remote

    def revert_last(self, message: str) -> str:
        self.git("revert", "--no-commit", "HEAD")
        self.git("commit", "-q", "-m", message)
        return self.head()

    def reset_to_origin(self) -> None:
        self.git("fetch", "-q", "origin", self.branch, timeout=180)
        self.git("reset", "-q", "--hard", f"origin/{self.branch}")
