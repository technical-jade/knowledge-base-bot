"""Разбор ответов Claude: намерение (режим route) и итоговый JSON режима write."""

import json
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

INTENTS = {"new", "edit", "ask_kb", "find", "chat", "delete", "undo", "help", "to_article", "clarify"}
STYLES = {"simple", "dev", "reference", "course"}
ACTIONS = {"created", "edited", "moved", "deleted"}
_SECTION = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")


class ParseError(Exception):
    pass


@dataclass
class Intent:
    intent: str
    args: dict = field(default_factory=dict)


def _json_objects(text: str) -> list[dict]:
    """Все JSON-объекты верхнего уровня в тексте, по порядку."""
    found = []
    dec = json.JSONDecoder()
    i = 0
    while (i := text.find("{", i)) != -1:
        try:
            obj, end = dec.raw_decode(text, i)
        except json.JSONDecodeError:
            i += 1
            continue
        if isinstance(obj, dict):
            found.append(obj)
        i = end
    return found


def last_json(text: str) -> dict:
    """Последний JSON-объект ответа (предпочтительно — из последнего блока ```json)."""
    blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    for raw in reversed(blocks):
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    objs = _json_objects(text)
    if not objs:
        raise ParseError("в ответе нет JSON")
    return objs[-1]


def _section(v) -> str | None:
    if isinstance(v, str) and _SECTION.match(v.strip().lower()):
        return v.strip().lower()
    return None


def _style(v) -> str | None:
    return v if v in STYLES else None


def parse_intent(text: str) -> Intent:
    obj = last_json(text)
    intent = obj.get("intent")
    if intent not in INTENTS:
        raise ParseError(f"неизвестное намерение: {intent!r}")
    args = obj.get("args") if isinstance(obj.get("args"), dict) else {}
    clean: dict = {}
    if intent == "new":
        clean = {"topic": str(args.get("topic") or "").strip(), "section": _section(args.get("section")), "style": _style(args.get("style"))}
        if not clean["topic"]:
            raise ParseError("new без темы")
    elif intent == "to_article":
        clean = {"section": _section(args.get("section")), "style": _style(args.get("style"))}
    elif intent == "edit":
        clean = {
            "target": str(args.get("target") or "current"),
            "instruction": str(args.get("instruction") or "").strip(),
            "move_to": _section(args.get("move_to")),
        }
    elif intent == "delete":
        clean = {"target": str(args.get("target") or "current")}
    elif intent == "ask_kb":
        clean = {"question": str(args.get("question") or "")}
    elif intent == "find":
        clean = {"query": str(args.get("query") or "")}
    elif intent == "clarify":
        clean = {"question": str(args.get("question") or "Уточните, что сделать?")}
    return Intent(intent, clean)


def safe_doc_path(p) -> str:
    """Путь внутри docs/: относительный, без .., только .md. Иначе ParseError."""
    if not isinstance(p, str) or not p:
        raise ParseError(f"некорректный путь: {p!r}")
    pp = PurePosixPath(p.strip())
    if pp.is_absolute() or ".." in pp.parts or not pp.parts or pp.parts[0] != "docs" or len(pp.parts) < 2:
        raise ParseError(f"путь вне docs/: {p}")
    if pp.suffix != ".md":
        raise ParseError(f"не .md: {p}")
    if any(not re.match(r"^[a-z0-9][a-z0-9._-]*$", part) for part in pp.parts[1:]):
        raise ParseError(f"имя не латиница/kebab-case: {p}")
    return str(pp)


@dataclass
class WriteResult:
    action: str
    files: list[str]
    primary: str | None
    section: str | None
    summary: str
    src: str | None = None  # для moved
    dst: str | None = None


def parse_write_result(text: str) -> WriteResult:
    obj = last_json(text)
    action = obj.get("action")
    if action not in ACTIONS:
        raise ParseError(f"неизвестное action: {action!r}")
    files = obj.get("files") or []
    if not isinstance(files, list):
        raise ParseError("files должен быть списком")
    files = [safe_doc_path(f) for f in files]
    primary = safe_doc_path(obj["primary"]) if obj.get("primary") else None
    summary = str(obj.get("summary") or "").strip().replace("\n", " ")[:200]
    res = WriteResult(action, files, primary, _section(obj.get("section")), summary)
    if action == "moved":
        res.src = safe_doc_path(obj.get("from"))
        res.dst = safe_doc_path(obj.get("to"))
    if action in ("created", "edited") and not primary:
        res.primary = files[0] if files else None
        if not res.primary:
            raise ParseError("нет primary и files")
    if action == "deleted" and not files:
        raise ParseError("deleted без files")
    return res
