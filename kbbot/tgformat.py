"""Markdown от Claude → HTML Telegram, с разбиением по 4096 символов.

Telegram понимает только b/i/u/s/code/pre/a/blockquote, поэтому заголовки
становятся жирными строками, списки — строками с «•», таблицы — блоком pre.
Каждый блок закрыт сам по себе, и разбиение идёт только между блоками —
теги не рвутся.
"""

import html
import re

from markdown_it import MarkdownIt
from markdown_it.token import Token

LIMIT = 4096
_md = MarkdownIt("commonmark").enable(["table", "strikethrough"])


def esc(s: str) -> str:
    return html.escape(s, quote=False)


def _attr(s: str) -> str:
    return html.escape(s, quote=True)


def _inline(tokens: list[Token]) -> str:
    out = []
    links: list[bool] = []  # открыт ли <a> для каждой вложенной ссылки
    for t in tokens:
        match t.type:
            case "text":
                out.append(esc(t.content))
            case "softbreak" | "hardbreak":
                out.append("\n")
            case "code_inline":
                out.append(f"<code>{esc(t.content)}</code>")
            case "strong_open":
                out.append("<b>")
            case "strong_close":
                out.append("</b>")
            case "em_open":
                out.append("<i>")
            case "em_close":
                out.append("</i>")
            case "s_open":
                out.append("<s>")
            case "s_close":
                out.append("</s>")
            case "link_open":
                href = str(t.attrGet("href") or "")
                ok = bool(re.match(r"^(https?|tg|mailto):", href))
                links.append(ok)
                if ok:
                    out.append(f'<a href="{_attr(href)}">')
            case "link_close":
                if links and links.pop():
                    out.append("</a>")
            case "image":
                out.append(esc(t.content or ""))
            case "html_inline":
                out.append(esc(t.content))
            case _:
                if t.content:
                    out.append(esc(t.content))
    return "".join(out)


def _plain_inline(tokens: list[Token]) -> str:
    return "".join(t.content if t.type in ("text", "code_inline") else "\n" if "break" in t.type else "" for t in tokens)


def blocks(md: str) -> list[str]:
    """Markdown → список самостоятельных HTML-блоков."""
    toks = _md.parse(md)
    res: list[str] = []
    stack: list[list] = []  # [тип списка, номер]
    pending_prefix = ""
    quote = 0
    i = 0

    def flush_item(text: str) -> None:
        depth = max(len(stack) - 1, 0)
        res.append("   " * depth + text)

    while i < len(toks):
        t = toks[i]
        typ = t.type
        if typ == "heading_open":
            res.append(f"<b>{_inline(toks[i + 1].children or [])}</b>")
            i += 3
            continue
        if typ == "paragraph_open":
            text = _inline(toks[i + 1].children or [])
            if pending_prefix:
                flush_item(pending_prefix + text)
                pending_prefix = ""
            elif stack:
                flush_item("   " + text)
            elif quote:
                res.append(f"<blockquote>{text}</blockquote>")
            else:
                res.append(text)
            i += 3
            continue
        if typ in ("bullet_list_open", "ordered_list_open"):
            start = int(t.attrGet("start") or 1)
            stack.append([typ, start])
        elif typ in ("bullet_list_close", "ordered_list_close"):
            stack.pop()
        elif typ == "list_item_open":
            kind, n = stack[-1]
            if kind == "bullet_list_open":
                pending_prefix = "• "
            else:
                pending_prefix = f"{n}. "
                stack[-1][1] = n + 1
        elif typ == "list_item_close":
            if pending_prefix:
                flush_item(pending_prefix.rstrip())
                pending_prefix = ""
        elif typ in ("fence", "code_block"):
            lang = (t.info or "").strip().split(" ")[0]
            cls = f' class="language-{_attr(lang)}"' if lang else ""
            res.append(f"<pre><code{cls}>{esc(t.content.rstrip())}</code></pre>")
        elif typ == "blockquote_open":
            quote += 1
        elif typ == "blockquote_close":
            quote -= 1
        elif typ == "hr":
            res.append("———")
        elif typ == "html_block":
            res.append(esc(t.content.strip()))
        elif typ == "table_open":
            rows: list[list[str]] = []
            j = i + 1
            row: list[str] = []
            while toks[j].type != "table_close":
                if toks[j].type == "inline":
                    row.append(_plain_inline(toks[j].children or []).strip())
                elif toks[j].type == "tr_close":
                    rows.append(row)
                    row = []
                j += 1
            res.append("<pre>" + esc("\n".join(" | ".join(r) for r in rows)) + "</pre>")
            i = j + 1
            continue
        i += 1
    return [b for b in res if b.strip()]


def _split_long(block: str, limit: int) -> list[str]:
    """Один блок длиннее лимита: pre режем по строкам, остальное — как текст."""
    m = re.match(r"^<pre>(<code[^>]*>)?(.*?)(</code>)?</pre>$", block, re.S)
    if m:
        open_code, body = m.group(1) or "", m.group(2)
        close_code = "</code>" if open_code else ""
        wrap = len("<pre></pre>") + len(open_code) + len(close_code)
        parts, cur = [], ""
        for line in body.split("\n"):
            while len(line) > limit - wrap:
                parts.append(line[: limit - wrap])
                line = line[limit - wrap:]
            if len(cur) + len(line) + 1 + wrap > limit:
                parts.append(cur)
                cur = line
            else:
                cur = f"{cur}\n{line}" if cur else line
        if cur:
            parts.append(cur)
        return [f"<pre>{open_code}{p}{close_code}</pre>" for p in parts]
    # Текст без разметки, чтобы не порвать тег
    plain = esc(html.unescape(re.sub(r"<[^>]+>", "", block)))
    out = []
    while len(plain) > limit:
        cut = plain.rfind(" ", 0, limit)
        cut = cut if cut > limit // 2 else limit
        # не резать посреди &amp;
        amp = plain.rfind("&", cut - 8, cut)
        if amp != -1 and ";" not in plain[amp:cut]:
            cut = amp
        out.append(plain[:cut])
        plain = plain[cut:].lstrip()
    out.append(plain)
    return out


def to_messages(md: str, limit: int = LIMIT) -> list[str]:
    msgs: list[str] = []
    cur = ""
    for b in blocks(md) or [esc(md)]:
        pieces = [b] if len(b) <= limit else _split_long(b, limit)
        for p in pieces:
            if not cur:
                cur = p
            elif len(cur) + 2 + len(p) <= limit:
                cur += "\n\n" + p
            else:
                msgs.append(cur)
                cur = p
    if cur:
        msgs.append(cur)
    return msgs or [""]


def plain_chunks(text: str, limit: int = LIMIT) -> list[str]:
    """Запасной вариант без разметки (если Telegram не принял HTML)."""
    out = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        cut = cut if cut > limit // 2 else limit
        out.append(text[:cut])
        text = text[cut:].lstrip("\n")
    out.append(text)
    return out
