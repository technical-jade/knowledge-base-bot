import os
import threading
import urllib.error
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer

import pytest

from kbbot.publish import PublishError, current, publish, releases, rollback
from kbbot.web import Handler


def build(tmp_path, text):
    out = tmp_path / "out"
    if out.exists():
        import shutil
        shutil.rmtree(out)
    (out / "devops").mkdir(parents=True)
    (out / "index.html").write_text(text)
    (out / "404.html").write_text("not found page")
    (out / "devops" / "index.html").write_text("devops")
    (out / "devops" / "a.html").write_text("article")
    return out


def test_publish_switch_prune_rollback(tmp_path):
    rel, site = tmp_path / "releases", tmp_path / "site"
    for i in range(7):
        publish(build(tmp_path, f"v{i}"), rel, site, f"sha{i}", keep=5)
        os.utime(rel / f"sha{i}", (1000 + i, 1000 + i))
    assert (site / "index.html").read_text() == "v6"
    assert [p.name for p in releases(rel)] == ["sha6", "sha5", "sha4", "sha3", "sha2"]
    rollback(rel, site)
    assert (site / "index.html").read_text() == "v5"
    rollback(rel, site)
    assert (site / "index.html").read_text() == "v4"


def test_publish_same_sha_twice(tmp_path):
    rel, site = tmp_path / "releases", tmp_path / "site"
    publish(build(tmp_path, "a"), rel, site, "abc")
    publish(build(tmp_path, "b"), rel, site, "abc")
    assert (site / "index.html").read_text() == "b"
    assert len(releases(rel)) == 2


def test_publish_rejects_empty_build_and_bad_sha(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(PublishError):
        publish(tmp_path / "empty", tmp_path / "r", tmp_path / "s", "abc")
    with pytest.raises(PublishError):
        publish(build(tmp_path, "x"), tmp_path / "r", tmp_path / "s", "../x")


def test_rollback_without_previous(tmp_path):
    publish(build(tmp_path, "a"), tmp_path / "r", tmp_path / "s", "a1")
    with pytest.raises(PublishError):
        rollback(tmp_path / "r", tmp_path / "s")
    assert current(tmp_path / "s").name == "a1"


@pytest.fixture
def server(tmp_path):
    rel, site = tmp_path / "releases", tmp_path / "site"
    publish(build(tmp_path, "home"), rel, site, "s1")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(site)))
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", tmp_path
    httpd.shutdown()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def get(url):
    opener = urllib.request.build_opener(NoRedirect)
    try:
        r = opener.open(url)
        return r.status, r.read().decode(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), e.headers


def test_web_serves_with_and_without_prefix(server):
    base, _ = server
    assert get(base + "/")[:2] == (200, "home")
    assert get(base + "/kb/")[:2] == (200, "home")
    assert get(base + "/devops/a.html")[:2] == (200, "article")
    assert get(base + "/kb/devops/a.html")[:2] == (200, "article")


def test_web_404_and_no_listing(server):
    base, tmp = server
    assert get(base + "/nope.html")[:2] == (404, "not found page")
    (tmp / "releases" / "s1" / "empty").mkdir()
    assert get(base + "/empty/")[0] == 404
    assert get(base + "/../../etc/passwd")[0] == 404


def test_web_redirect_keeps_prefix(server):
    base, _ = server
    status, _, headers = get(base + "/devops")
    assert status == 301 and headers["Location"] == "/kb/devops/"
    assert get(base + "/kb")[2]["Location"] == "/kb/"


def test_web_follows_switched_release(server, tmp_path):
    base, tmp = server
    publish(build(tmp, "new home"), tmp / "releases", tmp / "site", "s2")
    assert get(base + "/")[1] == "new home"
