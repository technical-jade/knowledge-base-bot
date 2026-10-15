import re

from kbbot.tgformat import blocks, plain_chunks, to_messages

ALLOWED = {"b", "i", "s", "u", "code", "pre", "a", "blockquote"}


def balanced(h: str) -> bool:
    stack = []
    for m in re.finditer(r"<(/?)([a-z]+)[^>]*>", h):
        close, tag = m.group(1), m.group(2)
        assert tag in ALLOWED, tag
        if close:
            if not stack or stack.pop() != tag:
                return False
        else:
            stack.append(tag)
    return not stack


def test_basic_markup():
    h = to_messages("# Заголовок\n\n**жирный** _курсив_ `код` ~~зачёрк~~ [ссылка](https://x.y/?a=1&b=2)")[0]
    assert "<b>Заголовок</b>" in h
    assert "<b>жирный</b>" in h and "<i>курсив</i>" in h and "<code>код</code>" in h and "<s>зачёрк</s>" in h
    assert '<a href="https://x.y/?a=1&amp;b=2">ссылка</a>' in h


def test_escaping_and_html():
    h = to_messages("a < b & c > d\n\n<script>alert(1)</script>")[0]
    assert "a &lt; b &amp; c &gt; d" in h
    assert "<script>" not in h and "&lt;script&gt;" in h


def test_bad_link_scheme_dropped():
    h = to_messages("[x](javascript:alert(1)) и [y](docs/a.md)")[0]
    assert "<a" not in h and "</a>" not in h and "x" in h


def test_lists_and_code():
    md = "- один\n- два\n  - вложенный\n\n1. первый\n2. второй\n\n```python\nprint('<hi>')\n```"
    h = to_messages(md)[0]
    assert "• один" in h and "• два" in h and "   • вложенный" in h
    assert "1. первый" in h and "2. второй" in h
    assert '<pre><code class="language-python">print(\'&lt;hi&gt;\')</code></pre>' in h


def test_table_becomes_pre():
    h = to_messages("| a | b |\n|---|---|\n| 1 | 2 |")[0]
    assert h == "<pre>a | b\n1 | 2</pre>"


def test_split_respects_limit_and_tags():
    md = "\n\n".join(f"**Пункт {i}** " + "слово " * 60 for i in range(60))
    msgs = to_messages(md)
    assert len(msgs) > 1
    assert all(len(m) <= 4096 for m in msgs)
    assert all(balanced(m) for m in msgs)


def test_huge_code_block_split():
    md = "```\n" + "\n".join("строка кода номер %d" % i for i in range(2000)) + "\n```"
    msgs = to_messages(md)
    assert len(msgs) > 1 and all(len(m) <= 4096 and m.startswith("<pre>") and balanced(m) for m in msgs)


def test_huge_paragraph_split_plain():
    msgs = to_messages("**" + "а&б " * 3000 + "**")
    assert all(len(m) <= 4096 for m in msgs)
    assert all(balanced(m) for m in msgs)


def test_blockquote_and_empty():
    assert blocks("> цитата") == ["<blockquote>цитата</blockquote>"]
    assert to_messages("") == [""]


def test_plain_chunks():
    parts = plain_chunks("x\n" * 5000)
    assert all(len(p) <= 4096 for p in parts) and "".join(parts).count("x") == 5000
