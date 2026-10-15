"""kb-bot-cli: тот же Core без Telegram — для самопроверки (ТЗ, раздел 9).

    kb-bot-cli "напиши гайд по Docker volumes"
    kb-bot-cli --choose dev "напиши гайд по Docker volumes"
    kb-bot-cli --reply 1000000007 "сделай короче"
    kb-bot-cli --tasks
"""

import argparse
import asyncio
import logging
import sys
import time

from .config import ConfigError, load
from .core import Core
from .log import setup_logging

# id «сообщений» CLI не пересекаются с id Telegram
CLI_ID_BASE = 1_000_000_000


class CliResponder:
    def __init__(self, core: Core, choice: str | None) -> None:
        self.core = core
        self.choice = choice
        self.t0 = time.monotonic()
        self.messages: list[str] = []

    def _ts(self) -> str:
        return f"[{time.monotonic() - self.t0:6.1f}s]"

    async def send(self, md: str, article: str | None = None) -> int:
        seq = int(self.core.store.get("cli_msg_seq") or CLI_ID_BASE) + 1
        self.core.store.set("cli_msg_seq", str(seq))
        if article:
            self.core.store.bind_message(seq, article)
        self.messages.append(md)
        print(f"{self._ts()} 💬 #{seq}" + (f" (статья {article})" if article else "") + f"\n{md}\n", flush=True)
        return seq

    async def status(self, text: str) -> None:
        print(f"{self._ts()} ⏳ {text}", flush=True)

    async def done(self) -> None:
        pass

    async def choose(self, question: str, options: list[tuple[str, str]], default: str, timeout: int) -> str:
        keys = [k for k, _ in options]
        print(f"{self._ts()} ❓ {question}\n   варианты: " + ", ".join(f"{k} — {v}" for k, v in options), flush=True)
        if self.choice in keys:
            key = self.choice
        elif sys.stdin.isatty():
            key = input(f"   выбор [{default}]: ").strip() or default
            key = key if key in keys else default
        else:
            key = default
        print(f"   → {key}", flush=True)
        return key


async def main() -> int:
    ap = argparse.ArgumentParser(prog="kb-bot-cli")
    ap.add_argument("--reply", type=int, help="ответ на сообщение с этим id")
    ap.add_argument("--choose", help="ответ на кнопки (стиль: simple/dev/reference/course; подтверждение: yes/no)")
    ap.add_argument("--tasks", action="store_true", help="журнал задач")
    ap.add_argument("text", nargs="*")
    a = ap.parse_args()
    try:
        cfg = load(require_token=False)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    # Логирование — как в службе (иначе ошибки логирования, роняющие бота,
    # CLI не воспроизводит), но в stderr, чтобы не смешивать с ответами
    setup_logging(cfg.bot_token)
    logging.getLogger().handlers[0].setStream(sys.stderr)
    core = Core(cfg)
    if a.tasks:
        for row in core.store.tasks(30):
            tid, started, intent, result, detail, sha, dur = row
            print(f"{tid:4} {time.strftime('%m-%d %H:%M', time.localtime(started))} {intent:9} {result or '…':8} "
                  f"{(dur or 0):6.0f}s {sha or '':8} {(detail or '')[:60]}")
        return 0
    text = " ".join(a.text)
    if not text:
        ap.print_help()
        return 2
    resp = CliResponder(core, a.choose)
    await core.handle(text, resp, a.reply)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
