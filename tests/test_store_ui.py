"""Номера статей, подсказки, каталог (иконки, даты), git log и экраны ui."""

import subprocess

from kbbot import articles as kb
from kbbot.gitops import Repo
from kbbot.store import Store


def test_ref_stable_and_distinct(tmp_path):
    s = Store(tmp_path / "s.db")
    a = s.ref("docs/devops/a.md")
    assert s.ref("docs/devops/a.md") == a
    assert s.ref("docs/devops/b.md") != a
    assert s.ref_path(a) == "docs/devops/a.md"
    assert s.ref_path(99999) is None


def test_ref_follows_rename(tmp_path):
    s = Store(tmp_path / "s.db")
    a = s.ref("docs/misc/a.md")
    s.rename_article("docs/misc/a.md", "docs/devops/a.md")
    assert s.ref_path(a) == "docs/devops/a.md"
    assert s.ref("docs/devops/a.md") == a


def test_prompt_roundtrip(tmp_path):
    s = Store(tmp_path / "s.db")
    s.bind_prompt(42, "edit", "docs/devops/a.md")
    assert s.prompt(42) == ("edit", "docs/devops/a.md")
    assert s.prompt(43) is None


def _docs(tmp_path):
    d = tmp_path / "docs"
    (d / "devops").mkdir(parents=True)
    (d / "devops" / "index.md").write_text("---\ntitle: DevOps\norder: 1\n---\n")
    (d / "own").mkdir()
    (d / "own" / "index.md").write_text("---\ntitle: Своё\nicon: 🧪\n---\n")
    (d / "devops" / "a.md").write_text("---\ntitle: A\nupdated: 2026-10-15\n---\n")
    (d / "courses" / "rust").mkdir(parents=True)
    (d / "courses" / "index.md").write_text("---\ntitle: Учебники\n---\n")
    (d / "courses" / "rust" / "index.md").write_text("---\ntitle: Rust\n---\n")
    for n in ("01-a.md", "02-b.md"):
        (d / "courses" / "rust" / n).write_text("---\ntitle: ch\n---\n")
    return tmp_path


def test_icons_frontmatter_and_defaults(tmp_path):
    repo = _docs(tmp_path)
    assert kb.icons(repo) == {"devops": "⚙️", "own": "🧪", "courses": "🎓"}


def test_updated_and_chapters(tmp_path):
    repo = _docs(tmp_path)
    a = {x.path: x for x in kb.articles(repo)}
    assert a["docs/devops/a.md"].updated == "2026-10-15"
    assert a["docs/courses/rust/index.md"].updated == ""
    assert kb.chapters(repo, "docs/courses/rust/index.md") == 2


def test_recent_commits(tmp_path):
    def g(*a):
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True)

    g("init", "-q", "-b", "main")
    for i, email in enumerate(("stan@x", "kb-bot@jade")):
        (tmp_path / "docs").mkdir(exist_ok=True)
        (tmp_path / "docs" / f"f{i}.md").write_text("x")
        g("add", "-A")
        g("-c", "user.name=n", "-c", f"user.email={email}", "commit", "-q", "-m", f"msg {i}")
    out = Repo(tmp_path, "n", "e").recent(10)
    assert [c.subject for c in out] == ["msg 1", "msg 0"]
    assert out[0].email == "kb-bot@jade" and out[0].files == ["docs/f1.md"]
    assert len(out[0].date) == 10


# ---------- экраны ----------

from kbbot import ui  # noqa: E402
from kbbot.articles import Article  # noqa: E402
from kbbot.gitops import Commit  # noqa: E402

SITE = "https://x/kb/"
SECS = {"devops": "DevOps", "misc": "Разное", "courses": "Учебники"}
ICONS = {"devops": "⚙️", "misc": "📦", "courses": "🎓"}
REFS: dict[str, int] = {}


def ref(path):
    return REFS.setdefault(path, 10**9 + len(REFS))  # большие номера — проверка длины


def arts(n, section="devops"):
    return [Article(f"docs/{section}/a{i:02}.md", f"Статья {i}", section, "", "2026-10-16") for i in range(n)]


def actions(screen):
    return [b.action for row in screen.buttons for b in row if b.action]


def labels(screen):
    return [b.label for row in screen.buttons for b in row]


def test_home_grid_and_counts():
    s = ui.home(SITE, SECS, ICONS, arts(3) + arts(1, "misc"))
    assert "4 статьи" in s.md and SITE in s.md
    assert [len(r) for r in s.buttons] == [2, 1, 1]
    assert s.buttons[0][0].label == "⚙️ DevOps (3)" and s.buttons[0][0].action == "sec:devops:0"
    assert s.buttons[-1][0].action == "recent"


def test_section_rows_and_pagination():
    a = arts(23)
    p0 = ui.section(SITE, "devops", SECS, ICONS, a, 0, ref)
    rows = p0.buttons
    assert len([r for r in rows if r[0].action.startswith("card:")]) == 10
    first = rows[0]
    r0 = ref("docs/devops/a00.md")
    assert [b.action for b in first] == [f"card:{r0}", f"ed:{r0}", f"del:{r0}"]
    assert [b.label for b in first[1:]] == ["✏️", "🗑"]
    nav = rows[-2]
    assert [b.label for b in nav] == ["1/3", "▶️"]
    p2 = ui.section(SITE, "devops", SECS, ICONS, a, 2, ref)
    assert [b.label for b in p2.buttons[-2]] == ["◀️", "3/3"]
    assert len([r for r in p2.buttons if r[0].action.startswith("card:")]) == 3
    assert [b.action for b in p0.buttons[-1]] == ["home", "new:devops"]
    assert "23 статьи" in p0.md


def test_section_page_out_of_range_clamped():
    s = ui.section(SITE, "devops", SECS, ICONS, arts(3), 7, ref)
    assert len([r for r in s.buttons if r[0].action.startswith("card:")]) == 3


def test_section_empty():
    s = ui.section(SITE, "misc", SECS, ICONS, arts(2), 0, ref)
    assert "Пока пусто" in s.md
    assert [b.action for b in s.buttons[-1]] == ["home", "new:misc"]


def test_card_article():
    a = Article("docs/devops/x.md", "Docker volumes", "devops", "Как хранить данные", "2026-10-15")
    s = ui.card(SITE, a, SECS, ICONS, ref, None)
    r = ref(a.path)
    assert "Docker volumes" in s.md and "Как хранить данные" in s.md and "2026-10-15" in s.md
    assert s.buttons[0][0].url == SITE + "devops/x.html"
    assert set(actions(s)) == {f"ed:{r}", f"mv:{r}", f"q:short:{r}", f"q:simple:{r}", f"q:examples:{r}",
                               f"q:facts:{r}", f"del:{r}", "sec:devops:0"}


def test_card_course_has_chapters_no_move():
    a = Article("docs/courses/rust/index.md", "Rust", "courses", "", "")
    s = ui.card(SITE, a, SECS, ICONS, ref, 4)
    assert "Глав: 4" in s.md
    assert not any(x.startswith("mv:") for x in actions(s))


def test_move_targets_exclude_current_and_courses():
    a = Article("docs/devops/x.md", "X", "devops")
    s = ui.move_targets(a, SECS, ICONS, ref)
    r = ref(a.path)
    assert actions(s) == [f"mvto:{r}:misc", f"card:{r}"]


def test_recent_undo_only_for_bot_last():
    a = [Article("docs/devops/x.md", "X", "devops"), Article("docs/courses/rust/index.md", "Rust", "courses")]
    cs = [Commit("abc1234", "2026-10-16", "kb-bot@jade", "Статья: X", ["docs/devops/x.md"]),
          Commit("def5678", "2026-10-15", "stan@x", "Учебник", ["docs/courses/rust/02-b.md", "docs/gone.md"])]
    s = ui.recent(cs, a, ref, "kb-bot@jade")
    assert "🤖" in s.md and "✍️" in s.md and "Статья: X" in s.md
    assert actions(s) == [f"card:{ref('docs/devops/x.md')}", f"card:{ref('docs/courses/rust/index.md')}", "undo", "home"]
    s2 = ui.recent(cs[1:], a, ref, "kb-bot@jade")
    assert "undo" not in actions(s2)


def test_result_buttons():
    r = ref("docs/devops/x.md")
    rows = ui.result_buttons(SITE, "docs/devops/x.md", ref)
    assert rows[0][0].url == SITE + "devops/x.html"
    assert [b.action for row in rows for b in row if b.action] == [f"ed:{r}", f"mv:{r}", "undo", f"del:{r}"]
    assert [b.action for row in ui.result_buttons(SITE, None, ref, deleted=True) for b in row] == ["undo", "home"]


def test_short_title():
    assert ui.short("x" * 40) == "x" * 29 + "…"
    assert ui.short("коротко") == "коротко"


def test_all_callback_data_fit_64_bytes_ascii():
    long = [Article(f"docs/devops/{'a' * 80}{i}.md", "Т" * 200, "devops", "", "") for i in range(30)]
    screens = [ui.home(SITE, SECS, ICONS, long), ui.section(SITE, "devops", SECS, ICONS, long, 1, ref),
               ui.card(SITE, long[0], SECS, ICONS, ref, None), ui.move_targets(long[0], SECS, ICONS, ref)]
    for s in screens:
        for row in s.buttons:
            for b in row:
                assert len(b.label) <= 32
                if b.action:
                    assert b.action.isascii() and len(b.action.encode()) <= 64
