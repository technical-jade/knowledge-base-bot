import subprocess

import pytest

from kbbot.gitops import ConflictError, GitError, Repo


def sh(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repos(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(origin), str(seed)], check=True, capture_output=True)
    sh(seed, "config", "user.name", "stan")
    sh(seed, "config", "user.email", "stan@x")
    (seed / "docs").mkdir()
    (seed / "docs" / "a.md").write_text("A\n")
    (seed / ".vitepress").mkdir()
    (seed / ".vitepress" / "config.ts").write_text("x\n")
    sh(seed, "add", "-A")
    sh(seed, "commit", "-q", "-m", "init")
    sh(seed, "push", "-q", "origin", "main")
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True, capture_output=True)
    return Repo(work), seed, tmp_path


def test_changed_and_boundary(repos):
    repo, _, _ = repos
    (repo.path / "docs" / "new.md").write_text("n")
    (repo.path / "docs" / "a.md").write_text("changed")
    (repo.path / ".vitepress" / "config.ts").write_text("evil")
    assert sorted(repo.changed()) == [".vitepress/config.ts", "docs/a.md", "docs/new.md"]
    assert repo.outside_docs() == [".vitepress/config.ts"]
    repo.rollback()
    assert repo.changed() == []
    assert not (repo.path / "docs" / "new.md").exists()


def test_commit_push_author(repos):
    repo, seed, _ = repos
    (repo.path / "docs" / "b.md").write_text("B")
    sha = repo.commit("Статья: B")
    assert repo.sync_and_push(repos[2] / "conflicts") is False
    sh(seed, "pull", "-q")
    assert sh(seed, "log", "-1", "--format=%an <%ae> %s").strip() == "kb-bot <kb-bot@jade> Статья: B"
    assert repo.last_commit()[1] == "kb-bot@jade"
    assert repo.last_commit()[0] == sha


def test_commit_without_changes(repos):
    with pytest.raises(GitError):
        repos[0].commit("пусто")


def test_rebase_brings_foreign_commit(repos):
    repo, seed, tmp = repos
    (seed / "docs" / "s.md").write_text("S")
    sh(seed, "add", "-A")
    sh(seed, "commit", "-q", "-m", "stan")
    sh(seed, "push", "-q")
    (repo.path / "docs" / "b.md").write_text("B")
    repo.commit("Статья: B")
    assert repo.sync_and_push(tmp / "conflicts") is True
    assert (repo.path / "docs" / "s.md").exists()


def test_conflict_saves_patch_and_resets(repos):
    repo, seed, tmp = repos
    (seed / "docs" / "a.md").write_text("STAN\n")
    sh(seed, "commit", "-q", "-am", "stan edit")
    sh(seed, "push", "-q")
    (repo.path / "docs" / "a.md").write_text("BOT\n")
    repo.commit("Правка: a")
    with pytest.raises(ConflictError) as e:
        repo.sync_and_push(tmp / "conflicts")
    assert e.value.files == ["docs/a.md"]
    assert "BOT" in e.value.patch.read_text()
    assert (repo.path / "docs" / "a.md").read_text() == "STAN\n"
    assert repo.changed() == []
    assert repo.last_commit()[2] == "stan edit"


def test_revert_mv_rm(repos):
    repo, _, tmp = repos
    (repo.path / "docs" / "b.md").write_text("B")
    repo.commit("Статья: B")
    repo.revert_last("Отмена: Статья: B")
    assert not (repo.path / "docs" / "b.md").exists()
    assert repo.last_commit()[2] == "Отмена: Статья: B"
    repo.mv("docs/a.md", "docs/devops/a.md")
    assert "docs/devops/a.md" in repo.changed()
    repo.rm(["docs/devops/a.md"])
    repo.commit("x")
    assert not (repo.path / "docs" / "devops" / "a.md").exists()


def test_pull_ff(repos):
    repo, seed, _ = repos
    (seed / "docs" / "c.md").write_text("C")
    sh(seed, "add", "-A")
    sh(seed, "commit", "-q", "-m", "c")
    sh(seed, "push", "-q")
    repo.pull_ff()
    assert (repo.path / "docs" / "c.md").exists()
