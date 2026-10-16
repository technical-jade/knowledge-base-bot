"""Запуск Claude через обёртку kb-claude-run и разбор stream-json."""

import asyncio
import json
import logging
import re
import signal
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from urllib.parse import urlparse

log = logging.getLogger("claude")

StatusCb = Callable[[str], Awaitable[None]] | None

_AUTH = re.compile(r"invalid api key|\b401\b|/login|oauth token|authentication_error|not logged in|token (?:has )?expired", re.I)
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class ClaudeError(Exception):
    pass


class ClaudeAuthError(ClaudeError):
    pass


class ClaudeTimeout(ClaudeError):
    pass


@dataclass
class ClaudeResult:
    text: str
    session_id: str | None
    cost: float = 0.0
    tools: list[str] = field(default_factory=list)


def status_for(name: str, inp: dict) -> str | None:
    """Строка статуса по вызову инструмента (ТЗ, раздел 4 «Статусы»)."""
    if name == "WebSearch":
        return f"Ищу: {str(inp.get('query', ''))[:80]}"
    if name == "WebFetch":
        host = urlparse(str(inp.get("url", ""))).hostname or "страницу"
        return f"Читаю: {host}"
    if name in ("Write", "Edit"):
        path = str(inp.get("file_path", ""))
        rel = path.split("/docs/", 1)[-1] if "/docs/" in path else path.removeprefix("docs/")
        return f"Пишу: {rel}"
    if name in ("Read", "Grep", "Glob"):
        return "Читаю базу…"
    return None


def parse_line(line: str) -> dict | None:
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


async def run(
    cmd: list[str],
    mode: str,
    prompt: str,
    resume: str | None = None,
    timeout: int = 1200,
    on_status: StatusCb = None,
) -> ClaudeResult:
    argv = [*cmd, mode]
    if resume:
        if not _UUID.match(resume):
            raise ClaudeError("некорректный id сессии")
        argv += ["--resume", resume]
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=16 * 1024 * 1024,
    )
    assert proc.stdin and proc.stdout and proc.stderr
    proc.stdin.write(prompt.encode())
    await proc.stdin.drain()
    proc.stdin.close()

    result: dict | None = None
    session_id: str | None = None
    tools: list[str] = []
    stderr_chunks: list[bytes] = []
    assistant_text: list[str] = []

    async def read_stderr() -> None:
        while chunk := await proc.stderr.read(4096):
            stderr_chunks.append(chunk)

    async def read_stdout() -> None:
        nonlocal result, session_id
        while raw := await proc.stdout.readline():
            ev = parse_line(raw.decode(errors="replace"))
            if not ev:
                continue
            t = ev.get("type")
            if ev.get("session_id"):
                session_id = ev["session_id"]
            if t == "assistant":
                for c in ev.get("message", {}).get("content", []):
                    if c.get("type") == "tool_use":
                        tools.append(c.get("name", ""))
                        s = status_for(c.get("name", ""), c.get("input") or {})
                        if s and on_status:
                            await on_status(s)
                    elif c.get("type") == "text":
                        assistant_text.append(c.get("text", ""))
            elif t == "result":
                result = ev

    err_task = asyncio.create_task(read_stderr())
    try:
        await asyncio.wait_for(asyncio.gather(read_stdout(), proc.wait()), timeout)
    except TimeoutError:
        await _kill(proc)
        raise ClaudeTimeout(f"Claude не уложился в {timeout // 60} мин") from None
    except asyncio.CancelledError:
        # Кнопка «Остановить» — процесс не должен продолжать писать в репозиторий
        await _kill(proc)
        raise
    finally:
        await asyncio.wait_for(err_task, 5) if not err_task.done() else None

    stderr = b"".join(stderr_chunks).decode(errors="replace")
    text = (result or {}).get("result") or ""
    is_error = bool((result or {}).get("is_error")) or proc.returncode not in (0, None) or result is None
    blob = "\n".join([text, stderr, *assistant_text[-2:]])
    if is_error:
        if _AUTH.search(blob):
            raise ClaudeAuthError(blob.strip()[:300])
        log.warning("claude error", extra={"rc": proc.returncode, "stderr": stderr[-500:], "text": text[:300]})
        raise ClaudeError((text or stderr or f"код {proc.returncode}").strip()[:500])
    return ClaudeResult(text=text, session_id=session_id, cost=float(result.get("total_cost_usd") or 0), tools=tools)


async def _kill(proc: asyncio.subprocess.Process) -> None:
    # sudo пересылает SIGTERM дочернему claude (проверено фактом); SIGKILL —
    # если не помогло. Страховка — `timeout` внутри kb-claude-run.
    for sig, wait in ((signal.SIGTERM, 10), (signal.SIGKILL, 5)):
        try:
            proc.send_signal(sig)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), wait)
            return
        except TimeoutError:
            continue
