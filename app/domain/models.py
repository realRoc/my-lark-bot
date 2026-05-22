"""Domain models for Feishu message parsing and Base writes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(slots=True, frozen=True)
class MessageResource:
    key: str
    resource_type: Literal["image", "file"]
    name: str


@dataclass(slots=True)
class ParsedIssue:
    description: str | None
    priority: str | None
    modules: list[str] = field(default_factory=list)
    surfaces: list[str] = field(default_factory=list)
    resources: list[MessageResource] = field(default_factory=list)


@dataclass(slots=True, frozen=True)
class UploadedAttachment:
    file_token: str
    name: str
