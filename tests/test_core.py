"""Конвейер изменения целиком на временных репозиториях с поддельными Claude и сборкой."""

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from kbbot.config import Config, Paths
from kbbot.core import Core

FAKES = Path(__file__).parent / "fakes"

GOOD = """---
title: Тест
description: d
tags: [t]
level: dev
created: 2026-10-16
updated: 2026-10-16
sources: []
---

# Тест
"""


def sh(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout


def result(action="created", path="docs/devops/test.md", summary="Статья: Тест", **extra):
    obj = {"action": action, "files": [path], "primary": path, "section": path.split("/")[1], "summary": summary, **extra}
    return "Готово.\n```json\n" + json.dumps(obj, ensure_ascii=False) + "\n```"


class Resp:
    def __init__(self, choice=None):
        self.sent, self.statuses, self.choice, self.asked = [], [], choice, []
        self.n = 0

    async def send(self, md, article=None):
        self.n += 1
        self.sent.append((md, article))
        return self.n

    async def status(self, text):
        self.statuses.append(text)

    async def done(self):
        pass

    async def choose(self, question, options, default, timeout):
        self.asked.append(question)
        return self.choice or default


@pytest.fixture
def env(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(origin), str(seed)], check=True, capture_output=True)
    sh(seed, "config", "user.name", "stan")
    sh(seed, "config", "user.email", "stan@x")
    for sec in ("devops", "misc"):
        (seed / "docs" / sec).mkdir(parents=True)
        (seed / "docs" / sec / "index.md").write_text(f"---\ntitle: {sec.upper()}\ndescription: d\n---\n")
    (seed / "docs" / "misc" / "old.md").write_text(GOOD.replace("Тест", "Старая"))
    (seed / ".vitepress" / "theme" / "components").mkdir(parents=True)
    (seed / ".vitepress" / "theme" / "components" / "Card.vue").write_text("<template/>")
    sh(seed, "add", "-A")
    sh(seed, "commit", "-q", "-m", "init")
    sh(seed, "push", "-q", "origin", "main")
    root = tmp_path / "srv"
    root.mkdir()
    repo = root / "repo"
    subprocess.run(["git", "clone", "-q", str(origin), str(repo)], check=True, capture_output=True)
    scenario = tmp_path / "scenario.json"
    paths = Paths(repo=repo, releases=root / "releases", site=root / "site", build_out=root / "build-out",
                  conflicts=root / "conflicts", state=tmp_path / "state", prompts=tmp_path)
    paths.state.mkdir()
    cfg = Config(bot_token="", allowed_id=1, paths=paths,
                 claude_cmd=[sys.executable, str(FAKES / "fake_claude.py"), str(scenario)],
                 build_cmd=[str(FAKES / "fake_build.sh"), str(paths.build_out)])

    def set_scenario(**sc):
        sc.setdefault("repo", str(repo))
        scenario.write_text(json.dumps(sc, ensure_ascii=False))
        for suffix in (".step", ".log"):
            scenario.with_suffix(suffix).unlink(missing_ok=True)

    def calls():
        log = scenario.with_suffix(".log")
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    return Core(cfg), set_scenario, calls, seed, root


def run(core, text, resp=None, reply_to=None):
    resp = resp or Resp()
    asyncio.run(core.handle(text, resp, reply_to))
    return resp


def test_new_article_full_pipeline(env):
    core, sc, calls, seed, root = env
    sc(route={"intent": "new", "args": {"topic": "Тест", "section": "devops", "style": None}},
       write=[{"files": {"docs/devops/test.md": GOOD}, "result": result()}])
    r = run(core, "напиши статью про тест", Resp(choice="simple"))
    assert r.asked, "стиль не указан — должны быть кнопки"
    assert "Стиль: simple" in calls()[1]["prompt"]
    md, article = r.sent[-1]
    assert md.startswith("Готово: https://jade.tail2b43aa.ts.net/kb/devops/test.html") and article == "docs/devops/test.md"
    sh(seed, "pull", "-q")
    assert (seed / "docs/devops/test.md").exists()
    assert sh(seed, "log", "-1", "--format=%an|%s").strip() == "kb-bot|Статья: Тест"
    assert (root / "site" / "index.html").exists()
    assert core.store.current_article == "docs/devops/test.md"
    assert core.store.article_session("docs/devops/test.md")
    assert {"Пишу: devops/test.md", "Собираю сайт…", "Публикую…"} <= set(r.statuses)


def test_style_in_request_skips_buttons(env):
    core, sc, calls, *_ = env
    sc(route={"intent": "new", "args": {"topic": "Тест", "style": "reference"}},
       write=[{"files": {"docs/devops/test.md": GOOD}, "result": result()}])
    r = run(core, "шпаргалка по тесту")
    assert not r.asked
    assert "Стиль: reference" in calls()[1]["prompt"]


def test_edit_resumes_article_session_and_reply(env):
    core, sc, calls, *_ = env
    sc(route={"intent": "new", "args": {"topic": "Тест", "style": "dev"}},
       write=[{"files": {"docs/devops/test.md": GOOD}, "result": result()}])
    r = run(core, "напиши")
    session = core.store.article_session("docs/devops/test.md")
    msg_id = r.n
    core.store.bind_message(777, "docs/devops/test.md")
    sc(route={"intent": "edit", "args": {"target": "current", "instruction": "короче"}},
       write=[{"files": {"docs/devops/test.md": GOOD + "\nкороче\n"}, "result": result("edited", summary="Правка: Тест — короче")}])
    r = run(core, "сделай короче", reply_to=777)
    assert "Статья: docs/devops/test.md" in calls()[1]["prompt"]
    assert calls()[1]["resume"] == session
    assert r.sent[-1][0].startswith("Обновлено:")
    assert msg_id


def test_boundary_violation_rolls_back(env):
    core, sc, calls, seed, _ = env
    sc(route={"intent": "new", "args": {"topic": "x", "style": "dev"}},
       write=[{"files": {".vitepress/config.ts": "evil", "docs/devops/test.md": GOOD}, "result": result()}])
    r = run(core, "напиши")
    assert "вне docs/" in r.sent[-1][0]
    assert core.repo.changed() == []
    assert not (core.cfg.paths.repo / ".vitepress/config.ts").exists()


def test_markdown_errors_fixed_by_claude(env):
    core, sc, calls, *_ = env
    bad = GOOD + "\n{{ secret }}\n"
    sc(route={"intent": "new", "args": {"topic": "x", "style": "dev"}},
       write=[{"files": {"docs/devops/test.md": bad}, "result": result()},
              {"files": {"docs/devops/test.md": GOOD}, "result": result()}])
    r = run(core, "напиши")
    assert r.sent[-1][0].startswith("Готово")
    fix = calls()[2]
    assert fix["prompt"].startswith("[fix]") and "{{" in fix["prompt"] and fix["resume"]


def test_markdown_errors_give_up_after_two(env):
    core, sc, calls, *_ = env
    sc(route={"intent": "new", "args": {"topic": "x", "style": "dev"}},
       write=[{"files": {"docs/devops/test.md": GOOD + "<script>x</script>"}, "result": result()}])
    r = run(core, "напиши")
    assert "Проверка Markdown не прошла" in r.sent[-1][0]
    assert len([c for c in calls() if c["mode"] == "write"]) == 3
    assert core.repo.changed() == []


def test_build_failure_rolls_back(env):
    core, sc, calls, _, root = env
    (root / "FAIL_BUILD").write_text("")
    sc(route={"intent": "new", "args": {"topic": "x", "style": "dev"}},
       write=[{"files": {"docs/devops/test.md": GOOD}, "result": result()}])
    r = run(core, "напиши")
    assert "Сборка не прошла" in r.sent[-1][0] and "TypeError" in r.sent[-1][0]
    assert core.repo.changed() == []


def test_conflict_reports_patch(env):
    core, sc, calls, seed, root = env
    (seed / "docs/misc/old.md").write_text(GOOD + "stan\n")
    sh(seed, "commit", "-q", "-am", "stan")
    sh(seed, "push", "-q")
    # Бот уже сделал pull до правки stan — эмулируем гонку: pull прошёл, затем stan пушит
    sc(route={"intent": "edit", "args": {"target": "docs/misc/old.md", "instruction": "x"}},
       write=[{"files": {"docs/misc/old.md": GOOD + "bot\n"}, "result": result("edited", "docs/misc/old.md", "Правка: Старая")}])
    orig_pull = core.repo.pull_ff
    core.repo.pull_ff = lambda: None  # pull «не увидел» коммит stan
    r = run(core, "в статье old поправь")
    core.repo.pull_ff = orig_pull
    msg = r.sent[-1][0]
    assert "Конфликт" in msg and "docs/misc/old.md" in msg
    patches = list((root / "conflicts").glob("*.patch"))
    assert patches and "bot" in patches[0].read_text()
    assert (core.cfg.paths.repo / "docs/misc/old.md").read_text() == GOOD + "stan\n"


def test_delete_needs_confirmation(env):
    core, sc, calls, seed, _ = env
    sc(route={"intent": "delete", "args": {"target": "docs/misc/old.md"}},
       write=[{"files": {}, "result": result("deleted", "docs/misc/old.md", "Удаление: Старая")}])
    r = run(core, "удали статью old", Resp(choice="no"))
    assert r.asked and "ничего не удаляю" in r.sent[-1][0]
    assert (core.cfg.paths.repo / "docs/misc/old.md").exists()
    assert not [c for c in calls() if c["mode"] == "write"]
    r = run(core, "удали статью old", Resp(choice="yes"))
    assert r.sent[-1][0].startswith("Удалено")
    sh(seed, "pull", "-q")
    assert not (seed / "docs/misc/old.md").exists()


def test_move(env):
    core, sc, calls, seed, _ = env
    sc(route={"intent": "edit", "args": {"target": "docs/misc/old.md", "instruction": "перенеси", "move_to": "devops"}},
       write=[{"files": {}, "result": result("moved", "docs/devops/old.md", "Перенос: Старая → devops",
                                              **{"from": "docs/misc/old.md", "to": "docs/devops/old.md"})},
              {"files": {}, "result": result("moved", "docs/devops/old.md", "Перенос: Старая → devops",
                                              **{"from": "docs/misc/old.md", "to": "docs/devops/old.md"})}])
    r = run(core, "перенеси old в devops")
    assert r.sent[-1][0].startswith("Перенесено: https://jade.tail2b43aa.ts.net/kb/devops/old.html")
    assert calls()[2]["prompt"].startswith("[links]")
    sh(seed, "pull", "-q")
    assert (seed / "docs/devops/old.md").exists() and not (seed / "docs/misc/old.md").exists()


def test_undo_only_bot_commits(env):
    core, sc, calls, seed, _ = env
    r = run(core, "/undo", Resp(choice="yes"))
    assert "не от бота" in r.sent[-1][0]
    sc(route={"intent": "new", "args": {"topic": "x", "style": "dev"}},
       write=[{"files": {"docs/devops/test.md": GOOD}, "result": result()}])
    run(core, "напиши")
    r = run(core, "/undo", Resp(choice="no"))
    assert "ничего не меняю" in r.sent[-1][0]
    r = run(core, "/undo", Resp(choice="yes"))
    assert r.sent[-1][0].startswith("Отменено")
    sh(seed, "pull", "-q")
    assert not (seed / "docs/devops/test.md").exists()
    assert sh(seed, "log", "-1", "--format=%s").strip() == "Отмена: Статья: Тест"


def test_chat_memory_and_reset(env):
    core, sc, calls, *_ = env
    sc(route={"intent": "chat", "args": {}}, read="HTTP/3 работает поверх QUIC")
    r = run(core, "чем HTTP/2 отличается от HTTP/3?")
    assert r.sent[-1][0] == "HTTP/3 работает поверх QUIC"
    first = core.store.chat_session
    run(core, "а подробнее?")
    assert calls()[-1]["resume"] == first and calls()[-1]["prompt"].startswith("[chat]")
    run(core, "/reset")
    assert core.store.chat_session is None


def test_auth_error_message(env):
    core, sc, *_ = env
    sc(auth_error=True, route={})
    r = run(core, "привет")
    assert "Claude не авторизован" in r.sent[-1][0] and "/etc/kb-claude/token" in r.sent[-1][0]


def test_list_and_help_without_claude(env):
    core, sc, calls, *_ = env
    sc(route={})
    r = run(core, "/list")
    assert "Старая" in r.sent[-1][0] and "https://jade.tail2b43aa.ts.net/kb/misc/old.html" in r.sent[-1][0]
    r = run(core, "/help")
    assert "/deploy" in r.sent[-1][0] and "Секреты" in r.sent[-1][0]
    assert calls() == []


def test_deploy(env):
    core, sc, calls, seed, root = env
    (seed / "docs/misc/new.md").write_text(GOOD)
    sh(seed, "add", "-A")
    sh(seed, "commit", "-q", "-m", "manual")
    sh(seed, "push", "-q")
    r = run(core, "/deploy")
    assert r.sent[-1][0].startswith("Опубликовано")
    assert (core.cfg.paths.repo / "docs/misc/new.md").exists()
    (seed / "docs/misc/bad.md").write_text("no frontmatter {{ x }}")
    sh(seed, "add", "-A")
    sh(seed, "commit", "-q", "-m", "bad")
    sh(seed, "push", "-q")
    r = run(core, "/deploy")
    assert "Проверка Markdown не прошла" in r.sent[-1][0]


def test_task_journal(env):
    core, sc, *_ = env
    sc(route={})
    run(core, "/help")
    rows = core.store.tasks()
    assert rows[0][2] == "/help" and rows[0][3] == "ok"
