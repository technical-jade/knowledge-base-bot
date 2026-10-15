"""Валидация аргументов обёрток: копия kb-claude-run без проверки пользователя
и с echo вместо claude."""
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def wrapper(tmp_path):
    src = (ROOT / "deploy" / "kb-claude-run").read_text()
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    for m in ("route", "read", "write", "about"):
        (prompts / f"{m}.md").write_text(m)
    token = tmp_path / "token"
    token.write_text("t")
    src = (src.replace('[[ $(id -un) == kb-claude ]] || die "запускать только от kb-claude"', "")
           .replace("/opt/kb-bot/prompts", str(prompts))
           .replace("/etc/kb-claude/token", str(token))
           .replace("cd /srv/kb/repo", f"cd {tmp_path}")
           .replace('exec timeout --kill-after=30 1500 "$HOME/.local/bin/claude"', "printf '%s\\n'"))
    w = tmp_path / "w.sh"
    w.write_text(src)
    w.chmod(0o755)
    return w


def run(w, *args):
    return subprocess.run([str(w), *args], capture_output=True, text=True, input="")


@pytest.mark.parametrize("args", [(), ("shell",), ("read", "--resume"), ("read", "--resume", "x;id"),
                                  ("read", "--resume", "12345678-1234-1234-1234-123456789abc", "x"),
                                  ("write", "--allowedTools", "Bash"), ("route", "--resume", "../../etc")])
def test_rejects(wrapper, args):
    assert run(wrapper, *args).returncode == 2


def test_modes_and_tools(wrapper):
    out = run(wrapper, "route").stdout.split("\n")
    assert out[out.index("--tools") + 1] == ""
    out = run(wrapper, "read").stdout.split("\n")
    assert out[out.index("--tools") + 1] == "Read,Glob,Grep,WebSearch,WebFetch"
    out = run(wrapper, "write", "--resume", "12345678-1234-1234-1234-123456789abc").stdout.split("\n")
    assert out[out.index("--tools") + 1] == "Read,Glob,Grep,Edit,Write,WebSearch,WebFetch"
    assert "Bash" in out[out.index("--disallowedTools") + 1]
    assert out[out.index("--resume") + 1] == "12345678-1234-1234-1234-123456789abc"
    assert "--model" in out and out[out.index("--model") + 1] == "opus"


def test_build_run_rejects_args():
    w = ROOT / "deploy" / "kb-build-run"
    for args in ([], ["rm"], ["build", "x"], ["install", "--network", "host"]):
        assert subprocess.run([str(w), *args], capture_output=True).returncode == 2
