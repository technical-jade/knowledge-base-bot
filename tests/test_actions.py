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
    assert acts(r.buttons[-1]) == ["undo", "home"]
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
    assert acts(r.buttons[-1]) == [f"card:{core.store.ref(OLD)}"]
