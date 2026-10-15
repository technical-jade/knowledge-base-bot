from kbbot.mdcheck import blank_code, check_files, check_text, component_names

ALLOWED = {"Card", "CardGrid", "Steps", "Compare", "Badge", "OutboundLink"}

FM = """---
title: Тест
description: Описание
tags: [docker]
level: dev
created: 2026-10-15
updated: 2026-10-15
sources: []
---
"""


def problems(body, rel="docs/devops/x.md", fm=FM):
    return [p.text for p in check_text(fm + body, rel, ALLOWED)]


def test_clean_article_passes():
    body = """# Заголовок

Текст с `inline <script>` и <b>жирным</b>.

<CardGrid>
<Card title="A" link="/devops/">Карточка</Card>
</CardGrid>

<card-grid></card-grid>

<Badge type="tip" text="new" />

::: tip
Совет
:::

```vue
<script setup>
const a = {{ x }}
</script>
<div @click="go" onclick="x()"></div>
```
"""
    assert problems(body) == []


def test_forbidden_tags():
    errs = problems("<script>alert(1)</script>\n<style>a{}</style>\n<IFRAME src=x>")
    assert any("<script>" in e for e in errs)
    assert any("<style>" in e for e in errs)
    assert any("<iframe>" in e for e in errs)


def test_event_handlers_and_directives():
    errs = problems('<img src="a.png" onerror="x()">\n<div @click="f"></div>\n<p v-html="raw"></p>\n<a :href="u">u</a>')
    assert any("onerror" in e for e in errs)
    assert any("@click" in e for e in errs)
    assert any("v-html" in e for e in errs)
    assert any(":href" in e for e in errs)


def test_handler_text_inside_quoted_value_is_fine():
    assert problems('<div title="click onload=1 here">x</div>') == []


def test_javascript_url_and_interpolation():
    errs = problems("[x](javascript:alert(1))\n\nСумма {{ 1 + 1 }}")
    assert any("javascript:" in e for e in errs)
    assert any("{{" in e for e in errs)


def test_multiline_interpolation():
    assert any("{{" in e for e in problems("{{\n  secret\n}}"))


def test_unknown_component():
    errs = problems("<Evil />\n<my-widget></my-widget>")
    assert any("<Evil>" in e for e in errs)
    assert any("<my-widget>" in e for e in errs)


def test_line_numbers_point_to_file():
    text = FM + "a\n\nb {{ x }}\n"
    (p,) = check_text(text, "docs/a.md", ALLOWED)
    assert p.line == FM.count("\n") + 3


def test_tilde_fence_and_longer_fence():
    body = "~~~\n<script>\n~~~\n\n````md\n```\n{{ x }}\n```\n````\n"
    assert problems(body) == []


def test_unclosed_fence_hides_rest_safely():
    # Незакрытый блок — до конца файла код; markdown-it рендерит так же
    assert problems("```\n{{ x }}\n") == []


def test_frontmatter_required_fields():
    errs = problems("text", fm="---\ntitle: A\n---\n")
    for key in ("description", "tags", "level", "created", "updated", "sources"):
        assert any(f"`{key}`" in e for e in errs)


def test_missing_frontmatter():
    assert any("нет frontmatter" in e for e in problems("# x", fm=""))


def test_bad_level_and_types():
    fm = FM.replace("level: dev", "level: pro").replace("tags: [docker]", "tags: docker")
    errs = problems("x", fm=fm)
    assert any("level" in e for e in errs)
    assert any("tags" in e for e in errs)


def test_index_needs_only_title_and_description():
    assert problems("x", rel="docs/devops/index.md", fm="---\ntitle: DevOps\ndescription: d\n---\n") == []


def test_home_layout_is_exempt():
    assert problems("x", rel="docs/index.md", fm="---\nlayout: home\n---\n") == []


def test_invalid_yaml():
    assert any("YAML" in e for e in problems("x", fm="---\ntitle: [\n---\n"))


def test_blank_code_keeps_lines():
    src = "a\n```\nb\nc\n```\nd `e` f"
    out = blank_code(src)
    assert out.count("\n") == src.count("\n")
    assert "b" not in out and "e" not in out


def test_check_files_uses_components_dir(tmp_path):
    comp = tmp_path / ".vitepress" / "theme" / "components"
    comp.mkdir(parents=True)
    (comp / "Steps.vue").write_text("<template/>")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text(FM + "<Steps>x</Steps>\n<Card/>\n")
    assert component_names(comp) == {"Steps", "Badge", "OutboundLink"}
    errs = check_files(tmp_path, ["docs/a.md", "docs/deleted.md", "README.txt"])
    assert [p.text for p in errs] == ["компонент <Card> не из белого списка"]


def test_mustache_in_inline_code_is_forbidden():
    # VitePress не защищает inline-код: найдено фактом на сборке
    errs = problems("| a | `{{ json .Mounts }}` |\n|---|---|\n")
    assert any("v-pre" in e for e in errs)


def test_vpre_container_allows_mustache():
    body = "::: v-pre\n`{{ json .Mounts }}`\n\n::: tip\n{{ x }}\n:::\n:::\n"
    assert problems(body) == []


def test_mustache_after_vpre_container_is_checked():
    assert problems("::: v-pre\nx\n:::\n{{ y }}\n") != []
