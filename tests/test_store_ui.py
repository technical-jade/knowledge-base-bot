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
