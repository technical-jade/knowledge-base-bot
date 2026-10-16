"""Сборка через sudo kb-build-run build и публикация релиза."""

import asyncio
import logging
from dataclasses import dataclass

from . import publish
from .config import Config

log = logging.getLogger("build")

BUILD_TIMEOUT = 15 * 60


@dataclass
class BuildResult:
    ok: bool
    log_tail: str


async def build(cfg: Config) -> BuildResult:
    proc = await asyncio.create_subprocess_exec(
        *cfg.build_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), BUILD_TIMEOUT)
    except TimeoutError:
        proc.terminate()
        await proc.wait()
        return BuildResult(False, "сборка не уложилась в 15 минут")
    except asyncio.CancelledError:
        # Остановленная задача не оставляет сборку, пишущую в build-out
        proc.terminate()
        await proc.wait()
        raise
    text = out.decode(errors="replace")
    # Цветовые коды vitepress в журнале не нужны
    text = "".join(ch for ch in text if ch == "\n" or ch >= " ").replace("[32m", "").replace("[31m", "").replace("[39m", "")
    tail = "\n".join(text.strip().splitlines()[-40:])
    if proc.returncode != 0:
        log.warning("build failed", extra={"rc": proc.returncode, "tail": tail[-1500:]})
    return BuildResult(proc.returncode == 0, tail)


async def publish_release(cfg: Config, sha: str) -> str:
    p = cfg.paths
    target = await asyncio.to_thread(publish.publish, p.build_out / "site", p.releases, p.site, sha)
    return target.name
