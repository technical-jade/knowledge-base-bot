"""Поддельный kb-claude-run для тестов: argv = <scenario.json> <mode> [--resume id].

scenario.json: {"route": {...intent json...}, "write": [шаг, ...], "read": "текст", "sleep": сек}
"sleep" — пауза перед итогом (проверка остановки); pid процесса — в <scenario>.pid.
Шаг write: {"files": {"docs/x.md": "содержимое"}, "result": "текст ответа"}.
Шаги выполняются по очереди (номер хранится рядом со сценарием).
Рабочая папка — репозиторий (cwd).
"""
import json
import os
import sys
import time
import uuid
from pathlib import Path

scenario_path = Path(sys.argv[1])
mode = sys.argv[2]
resume = sys.argv[4] if len(sys.argv) > 4 else None
sc = json.loads(scenario_path.read_text())
prompt = sys.stdin.read()
log = scenario_path.with_suffix(".log")
with log.open("a") as f:
    f.write(json.dumps({"mode": mode, "resume": resume, "prompt": prompt}, ensure_ascii=False) + "\n")
sid = resume or str(uuid.uuid4())

def emit(o):
    print(json.dumps(o, ensure_ascii=False), flush=True)

emit({"type": "system", "subtype": "init", "session_id": sid, "tools": []})
if sc.get("auth_error"):
    emit({"type": "result", "subtype": "success", "is_error": True, "result": "Invalid API key · Please run /login", "session_id": sid})
    sys.exit(1)
if mode == "route":
    text = json.dumps(sc["route"], ensure_ascii=False)
elif mode == "read":
    text = sc.get("read", "ответ")
else:
    step_file = scenario_path.with_suffix(".step")
    n = int(step_file.read_text()) if step_file.exists() else 0
    step = sc["write"][min(n, len(sc["write"]) - 1)]
    step_file.write_text(str(n + 1))
    repo = Path(sc["repo"])
    for rel, content in step.get("files", {}).items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        emit({"type": "assistant", "session_id": sid, "message": {"content": [
            {"type": "tool_use", "name": "Write", "input": {"file_path": str(p)}}]}})
    text = step["result"]
scenario_path.with_suffix(".pid").write_text(str(os.getpid()))
time.sleep(sc.get("sleep", 0))
emit({"type": "result", "subtype": "success", "is_error": False, "result": text, "session_id": sid, "total_cost_usd": 0.01})
