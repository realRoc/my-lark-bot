"""Message parsing helpers for Feishu IM events."""

from __future__ import annotations

import json
import re
from typing import Any

from app.domain.models import MessageResource, ParsedIssue


PRIORITY_RE = re.compile(r"(?<![A-Z0-9])P([0-9])(?![A-Z0-9])", re.IGNORECASE)
MENTION_FALLBACK_RE = re.compile(r"@_user_\d+")
WHITESPACE_RE = re.compile(r"[ \t]+")
NEWLINE_RE = re.compile(r"\n{3,}")


def parse_issue_from_message(
    *,
    message_type: str,
    raw_content: str,
    mentions: list[dict[str, Any]] | None,
    module_aliases: dict[str, tuple[str, ...]],
    allowed_priorities: set[str],
    allowed_surfaces: tuple[str, ...],
) -> ParsedIssue:
    content = _safe_json_loads(raw_content)
    resources: list[MessageResource] = []

    if message_type == "text":
        description = _cleanup_text(str(content.get("text", "")), mentions)
    elif message_type == "post":
        description, resources = _parse_post_content(content, mentions)
    else:
        description = None

    priority = _detect_priority(description, allowed_priorities)
    modules = _detect_modules(description, module_aliases)
    surfaces = _detect_surfaces(description, allowed_surfaces)
    return ParsedIssue(
        description=description or None,
        priority=priority,
        modules=modules,
        surfaces=surfaces,
        resources=resources,
    )


def _safe_json_loads(raw_content: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw_content or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _cleanup_text(text: str, mentions: list[dict[str, Any]] | None) -> str:
    value = text
    for mention in mentions or []:
        key = mention.get("key")
        if key:
            value = value.replace(str(key), "")
    value = MENTION_FALLBACK_RE.sub("", value)
    value = value.replace("\xa0", " ")
    lines = [WHITESPACE_RE.sub(" ", line).strip() for line in value.splitlines()]
    compact = "\n".join(line for line in lines if line)
    return NEWLINE_RE.sub("\n\n", compact).strip()


def _parse_post_content(
    content: dict[str, Any], mentions: list[dict[str, Any]] | None
) -> tuple[str | None, list[MessageResource]]:
    if isinstance(content.get("content"), list):
        paragraphs = content["content"]
    else:
        locale_payload = next(
            (value for value in content.values() if isinstance(value, dict) and "content" in value),
            None,
        )
        paragraphs = locale_payload.get("content", []) if locale_payload else []

    if not paragraphs:
        return None, []

    text_parts: list[str] = []
    resources: list[MessageResource] = []
    for paragraph in paragraphs:
        if not isinstance(paragraph, list):
            continue
        paragraph_text: list[str] = []
        for node in paragraph:
            if not isinstance(node, dict):
                continue
            tag = str(node.get("tag", "")).lower()
            if tag in {"text", "a", "md"}:
                paragraph_text.append(str(node.get("text", "")))
            elif tag in {"at"}:
                continue
            elif tag in {"img", "image"}:
                key = str(node.get("image_key", "")).strip()
                if key:
                    resources.append(MessageResource(key=key, resource_type="image", name="image.png"))
            elif tag in {"media", "file"}:
                key = str(node.get("file_key", "")).strip()
                if key:
                    resources.append(MessageResource(key=key, resource_type="file", name=str(node.get("file_name", "attachment"))))
        if paragraph_text:
            text_parts.append("".join(paragraph_text))

    description = _cleanup_text("\n".join(text_parts), mentions)
    return description or None, resources


def _detect_priority(text: str | None, allowed_priorities: set[str]) -> str | None:
    if not text:
        return None
    for match in PRIORITY_RE.finditer(text):
        candidate = f"P{match.group(1)}".upper()
        if candidate in allowed_priorities:
            return candidate
    return None


def _detect_modules(text: str | None, module_aliases: dict[str, tuple[str, ...]]) -> list[str]:
    if not text:
        return []
    lowered = text.lower()
    matched: list[str] = []
    for module_name, aliases in module_aliases.items():
        if any(alias and alias in lowered for alias in aliases):
            matched.append(module_name)
    return matched


def _detect_surfaces(text: str | None, allowed_surfaces: tuple[str, ...]) -> list[str]:
    if not text:
        return []
    lowered = text.lower()
    matched: list[str] = []
    for surface in allowed_surfaces:
        pattern = re.compile(rf"(?<![a-z0-9]){re.escape(surface)}(?![a-z0-9])")
        if pattern.search(lowered):
            matched.append(surface)
    return matched
