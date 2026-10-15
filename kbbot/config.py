"""Настройки бота: секреты из окружения (/etc/kb-bot/env), пути — константы ТЗ.

Пути можно переопределить переменными KB_* — это нужно тестам и самопроверке.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

SITE_URL = "https://jade.tail2b43aa.ts.net/kb/"


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Paths:
    repo: Path
    releases: Path
    site: Path
    build_out: Path
    conflicts: Path
    state: Path
    prompts: Path


def paths() -> Paths:
    root = Path(os.getenv("KB_ROOT", "/srv/kb"))
    return Paths(
        repo=Path(os.getenv("KB_REPO", root / "repo")),
        releases=root / "releases",
        site=root / "site",
        build_out=root / "build-out",
        conflicts=root / "conflicts",
        state=Path(os.getenv("KB_STATE", "/var/lib/kb-bot")),
        prompts=Path(os.getenv("KB_PROMPTS", Path(__file__).resolve().parent.parent / "prompts")),
    )


def _split(raw: str) -> list[str]:
    return raw.split()


@dataclass(frozen=True)
class Config:
    bot_token: str
    allowed_id: int
    paths: Paths
    site_url: str = SITE_URL
    # Команды запуска; в тестах подменяются заглушками
    claude_cmd: list[str] = field(default_factory=lambda: ["sudo", "-n", "-u", "kb-claude", "/usr/local/bin/kb-claude-run"])
    build_cmd: list[str] = field(default_factory=lambda: ["sudo", "-n", "/usr/local/bin/kb-build-run", "build"])
    task_timeout: int = 20 * 60
    route_timeout: int = 180
    style_timeout: int = 10 * 60
    git_name: str = "kb-bot"
    git_email: str = "kb-bot@jade"

    def __repr__(self) -> str:
        # Токен не должен попасть в лог даже при случайном print(config)
        return f"Config(allowed_id=***, repo={str(self.paths.repo)!r})"


def load(require_token: bool = True) -> Config:
    token = os.getenv("BOT_TOKEN", "").strip()
    if require_token and not token:
        raise ConfigError("BOT_TOKEN не задан (/etc/kb-bot/env)")
    raw_id = os.getenv("ALLOWED_ID", "").strip()
    try:
        allowed = int(raw_id)
    except ValueError:
        raise ConfigError("ALLOWED_ID должен быть числом") from None
    over = {}
    if os.getenv("KB_CLAUDE_CMD"):
        over["claude_cmd"] = _split(os.environ["KB_CLAUDE_CMD"])
    if os.getenv("KB_BUILD_CMD"):
        over["build_cmd"] = _split(os.environ["KB_BUILD_CMD"])
    if os.getenv("KB_TASK_TIMEOUT"):
        over["task_timeout"] = int(os.environ["KB_TASK_TIMEOUT"])
    return Config(bot_token=token, allowed_id=allowed, paths=paths(), **over)
