import pytest

from kbbot.intent import ParseError, last_json, parse_intent, parse_write_result, safe_doc_path


def test_parse_intent_variants():
    it = parse_intent('{"intent": "new", "args": {"topic": "nginx", "section": "DevOps", "style": "simple"}}')
    assert it.intent == "new" and it.args == {"topic": "nginx", "section": "devops", "style": "simple"}
    it = parse_intent('Вот: ```json\n{"intent":"edit","args":{"target":"current","instruction":"короче"}}\n```')
    assert it.args == {"target": "current", "instruction": "короче", "move_to": None}
    assert parse_intent('{"intent":"chat","args":{}}').intent == "chat"
    assert parse_intent('{"intent":"new","args":{"topic":"x","style":"bogus","section":"../etc"}}').args["style"] is None


def test_parse_intent_errors():
    with pytest.raises(ParseError):
        parse_intent("нет json")
    with pytest.raises(ParseError):
        parse_intent('{"intent":"rm -rf"}')
    with pytest.raises(ParseError):
        parse_intent('{"intent":"new","args":{}}')


def test_last_json_prefers_last_block():
    t = 'a {"x": 1} b\n```json\n{"action": "edited", "files": []}\n```\nи {"y": {'
    assert last_json(t) == {"action": "edited", "files": []}


@pytest.mark.parametrize("p", ["/etc/passwd", "docs/../x.md", "src/a.md", "docs/a.txt", "docs/Привет.md", "docs", "", None, "docs/A.md"])
def test_safe_doc_path_rejects(p):
    with pytest.raises(ParseError):
        safe_doc_path(p)


def test_safe_doc_path_ok():
    assert safe_doc_path("docs/courses/rust/01-intro.md") == "docs/courses/rust/01-intro.md"


def test_write_result():
    r = parse_write_result('Готово.\n```json\n{"action":"created","files":["docs/devops/a.md"],"primary":"docs/devops/a.md","section":"devops","summary":"Статья: A"}\n```')
    assert r.primary == "docs/devops/a.md" and r.summary == "Статья: A"
    r = parse_write_result('{"action":"moved","from":"docs/a/x.md","to":"docs/b/x.md","files":[],"summary":"Перенос"}')
    assert (r.src, r.dst) == ("docs/a/x.md", "docs/b/x.md")
    with pytest.raises(ParseError):
        parse_write_result('{"action":"created","files":["../../etc/x.md"]}')
    with pytest.raises(ParseError):
        parse_write_result('{"action":"deleted","files":[]}')
