"""Кнопки: навигация, ForceReply-подсказки, действия со статьями, /recent, /find."""

import asyncio

from test_core import GOOD, Resp, env, result, run, sh  # noqa: F401 — env — фикстура

from kbbot.ui import PRESETS

OLD = "docs/misc/old.md"


def press(core, action, resp=None, mid=5):
    resp = resp or Resp()
    asyncio.run(core.handle_action(action, resp, mid))
    return resp


def acts(rows):
    return [b.action for row in rows or [] for b in row if b.action]


def test_navigation_edits_message_without_claude(env):
    core, sc, calls, *_ = env
    sc(route={})
    r = press(core, "home")
    assert r.edits[-1][0] == 5 and "База знаний" in r.edits[-1][1]
    r = press(core, "sec:misc:0")
    ref = core.store.ref(OLD)
    assert f"card:{ref}" in acts(r.edits[-1][2])
    r = press(core, f"card:{ref}")
    assert "Старая" in r.edits[-1][1] and f"q:short:{ref}" in acts(r.edits[-1][2])
    r = press(core, f"mv:{ref}")
    assert f"mvto:{ref}:devops" in acts(r.edits[-1][2])
    press(core, "noop")
    assert calls() == []


def test_stale_ref_shows_sections(env):
    core, sc, calls, *_ = env
    gone = core.store.ref("docs/misc/gone.md")
    for action in (f"card:{gone}", f"ed:{gone}", f"q:short:{gone}", f"del:{gone}", "card:99999", "sec:nope:0"):
        r = press(core, action)
        md, buttons = r.edits[-1][1], r.edits[-1][2]
        assert "уже нет" in md and "sec:misc:0" in acts(buttons), action
    assert calls() == []


def test_edit_button_force_reply_skips_router(env):
    core, sc, calls, *_ = env
    ref = core.store.ref(OLD)
    r = press(core, f"ed:{ref}")
    assert "Что изменить" in r.asks[-1] and "Старая" in r.asks[-1]
    sc(route={"intent": "chat"},
       write=[{"files": {OLD: GOOD.replace("Тест", "Старая") + "\nпример\n"}, "result": result("edited", OLD, "Правка: Старая")}])
    r2 = run(core, "добавь пример", reply_to=r.n)
    assert [c["mode"] for c in calls()] == ["write"]
    assert f"Статья: {OLD}" in calls()[0]["prompt"] and "добавь пример" in calls()[0]["prompt"]
    assert r2.sent[-1][0].startswith("Обновлено:")
    assert "undo" in acts(r2.buttons[-1]) and f"ed:{ref}" in acts(r2.buttons[-1])


def test_command_in_reply_to_prompt_is_command(env):
    core, sc, calls, *_ = env
    r = press(core, f"ed:{core.store.ref(OLD)}")
    r2 = run(core, "/help", reply_to=r.n)
    assert "/deploy" in r2.sent[-1][0] and calls() == []


def test_new_in_section_button(env):
    core, sc, calls, *_ = env
    r = press(core, "new:devops")
    assert "Тема новой статьи" in r.asks[-1]
    sc(write=[{"files": {"docs/devops/test.md": GOOD}, "result": result()}])
    r2 = run(core, "Docker volumes", Resp(choice="reference"), reply_to=r.n)
    p = calls()[0]["prompt"]
    assert calls()[0]["mode"] == "write" and "Тема: Docker volumes" in p and "раздел devops" in p and "Стиль: reference" in p
    assert r2.sent[-1][0].startswith("Готово:")


def test_new_in_unknown_section(env):
    core, sc, calls, *_ = env
    r = press(core, "new:courses")  # раздела courses в тестовом репо нет
    assert "уже нет" in r.edits[-1][1] and not r.asks


def test_quick_edit(env):
    core, sc, calls, *_ = env
    sc(write=[{"files": {OLD: GOOD.replace("Тест", "Старая") + "\nкороче\n"}, "result": result("edited", OLD, "Правка: Старая")}])
    r = press(core, f"q:short:{core.store.ref(OLD)}")
    assert PRESETS["short"][1] in calls()[0]["prompt"] and f"Статья: {OLD}" in calls()[0]["prompt"]
    assert r.sent[-1][0].startswith("Обновлено:")


def test_move_to_button(env):
    core, sc, calls, seed, _ = env
    moved = result("moved", "docs/devops/old.md", "Перенос: Старая → devops", **{"from": OLD, "to": "docs/devops/old.md"})
    sc(write=[{"files": {}, "result": moved}, {"files": {}, "result": moved}])
    ref = core.store.ref(OLD)
    r = press(core, f"mvto:{ref}:devops")
    assert "Перенести в раздел: devops" in calls()[0]["prompt"]
    assert r.sent[-1][0].startswith("Перенесено:")
    assert core.store.ref_path(ref) == "docs/devops/old.md"


def test_delete_button_confirms(env):
    core, sc, calls, seed, _ = env
    sc(write=[{"files": {}, "result": result("deleted", OLD, "Удаление: Старая")}])
    r = press(core, f"del:{core.store.ref(OLD)}", Resp(choice="yes"))
    assert r.asked and r.sent[-1][0].startswith("Удалено")
    assert acts(r.buttons[-1]) == ["undo", "+home"]
    sh(seed, "pull", "-q")
    assert not (seed / OLD).exists()


def test_undo_button(env):
    core, sc, calls, *_ = env
    r = press(core, "undo", Resp(choice="yes"))
    assert "не от бота" in r.sent[-1][0]


def test_recent_command_and_button(env):
    core, sc, calls, *_ = env
    r = run(core, "/recent")
    assert "Последние изменения" in r.sent[-1][0] and "init" in r.sent[-1][0]
    assert f"card:{core.store.ref(OLD)}" in acts(r.buttons[-1])
    r = press(core, "recent")
    assert "Последние изменения" in r.edits[-1][1]


def test_find_answer_gets_card_buttons(env):
    core, sc, calls, *_ = env
    sc(read=f"Нашёл: [Старая](https://jade.tail2b43aa.ts.net/kb/misc/old.html)")
    r = run(core, "/find старое")
    assert acts(r.buttons[-1]) == [f"+card:{core.store.ref(OLD)}"]


# ---------- остановка ----------

import dataclasses  # noqa: E402
import os  # noqa: E402
import sys  # noqa: E402

import pytest  # noqa: E402

from kbbot.builder import build  # noqa: E402


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_stop_running_task_kills_claude_and_rolls_back(env):
    core, sc, calls, *_ = env
    sc(sleep=60, write=[{"files": {OLD: "испорчено"}, "result": result("edited", OLD)}])
    pidfile = core.cfg.claude_cmd[-1].replace(".json", ".pid")
    resp, other = Resp(), Resp()

    async def go():
        job = asyncio.create_task(core.handle_action(f"q:short:{core.store.ref(OLD)}", resp, 5))
        for _ in range(200):
            if os.path.exists(pidfile):
                break
            await asyncio.sleep(0.05)
        stop = next(a for a in resp.stops if a)
        await core.handle_action(stop, other, 6)
        await asyncio.wait_for(job, 20)
        return int(open(pidfile).read())

    pid = asyncio.run(go())
    assert resp.sent[-1][0].startswith("✖️ Остановлено")
    assert not other.sent  # успешная остановка — итог сообщает сама задача
    assert core.repo.changed() == []
    assert (core.cfg.paths.repo / OLD).read_text() != "испорчено"
    assert core.store.tasks(1)[0][3] == "stopped"
    assert not _alive(pid)


def test_stop_finished_task(env):
    core, *_ = env
    r = press(core, "stop:9999")
    assert "уже завершена" in r.sent[-1][0]


def test_stop_refused_in_critical_phase(env):
    core, *_ = env

    async def go():
        dummy = asyncio.create_task(asyncio.sleep(5))
        core._running[7] = dummy
        core._critical.add(7)
        r = Resp()
        await core.handle_action("stop:7", r, 1)
        cancelled = dummy.cancelled()
        dummy.cancel()
        return r, cancelled

    r, cancelled = asyncio.run(go())
    assert "Уже публикуется" in r.sent[-1][0] and not cancelled


def test_pipeline_drops_stop_button_before_commit(env):
    core, sc, calls, *_ = env
    sc(write=[{"files": {OLD: GOOD.replace("Тест", "Старая") + "\nx\n"}, "result": result("edited", OLD)}])
    r = press(core, f"q:short:{core.store.ref(OLD)}")
    assert r.sent[-1][0].startswith("Обновлено")
    assert r.stops[0].startswith("stop:") and None in r.stops


def test_build_cancel_terminates_process(env):
    core, *_ = env
    cfg = dataclasses.replace(core.cfg, build_cmd=[sys.executable, "-c", "import time; time.sleep(60)"])

    async def go():
        t = asyncio.create_task(build(cfg))
        await asyncio.sleep(1)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t

    asyncio.run(go())
    out = os.popen("pgrep -f '^[^ ]*python[^ ]* -c import time; time.sleep'").read().split()
    assert not out


# ---------- после ревью ----------


def test_plus_actions_open_new_message(env):
    core, *_ = env
    r = press(core, f"+card:{core.store.ref(OLD)}")
    assert not r.edits and "Старая" in r.sent[-1][0]


def test_second_stop_press_does_not_recancel(env):
    core, *_ = env

    async def go():
        t = asyncio.create_task(asyncio.sleep(5))
        core._running[8] = t
        assert core.stop(8) is None
        again = core.stop(8)
        n = t.cancelling()
        t.cancel()
        return again, n

    again, n = asyncio.run(go())
    assert "Останавлива" in again and n == 1


def test_stop_gives_immediate_feedback(env):
    core, *_ = env

    async def go():
        t = asyncio.create_task(asyncio.sleep(5))
        core._running[9] = t
        r = Resp()
        await core.handle_action("stop:9", r, 77)
        t.cancel()
        return r

    r = asyncio.run(go())
    assert r.edits[-1][0] == 77 and "Останавливаю" in r.edits[-1][1] and not r.edits[-1][2]


def test_stop_during_git_thread_waits_for_it(env, monkeypatch):
    core, sc, calls, *_ = env
    import time as _time
    state = {}
    real_pull = core.repo.pull_ff

    def slow_pull():
        state["started"] = True
        _time.sleep(1.0)
        real_pull()
        state["done"] = _time.monotonic()

    monkeypatch.setattr(core.repo, "pull_ff", slow_pull)
    resp = Resp()

    async def go():
        job = asyncio.create_task(core.handle_action(f"q:short:{core.store.ref(OLD)}", resp, 5))
        while not state.get("started"):
            await asyncio.sleep(0.02)
        core.stop(next(iter(core._running)))
        await job
        return _time.monotonic()

    ended = asyncio.run(go())
    assert "done" in state and state["done"] <= ended  # поток git завершён до освобождения замка
    assert resp.sent[-1][0].startswith("✖️ Остановлено")
    assert calls() == []


def test_rollback_error_does_not_mask_stop(env, monkeypatch):
    core, sc, calls, *_ = env
    from kbbot.gitops import GitError
    sc(sleep=60, write=[{"files": {OLD: "x"}, "result": result("edited", OLD)}])
    pidfile = core.cfg.claude_cmd[-1].replace(".json", ".pid")
    resp = Resp()

    async def go():
        job = asyncio.create_task(core.handle_action(f"q:short:{core.store.ref(OLD)}", resp, 5))
        while not os.path.exists(pidfile):
            await asyncio.sleep(0.05)

        def broken():
            raise GitError("index.lock")

        monkeypatch.setattr(core.repo, "rollback", broken)
        core.stop(next(iter(core._running)))
        await job

    asyncio.run(go())
    assert resp.sent[-1][0].startswith("✖️ Остановлено")
    assert core.store.tasks(1)[0][3] == "stopped"
