"""Ngữ cảnh truyền vào mọi handler op."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from sagareel.container import Services

Role = Literal["owner", "viewer"]


@dataclass
class OpContext:
    services: Services
    actor: str  # "user:<tên>" | "llm:<model>" | "agent:<tên>"
    role: Role
    project_id: int | None = None
    dry_run: bool = False
    run_overrides: dict = field(default_factory=dict)
