"""Ngữ cảnh truyền vào mọi handler op."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from sagareel.container import Services

if TYPE_CHECKING:
    from sagareel.core.uow import UnitOfWork

Role = Literal["owner", "viewer"]


@dataclass
class OpContext:
    services: Services
    actor: str  # "user:<tên>" | "llm:<model>" | "agent:<tên>"
    role: Role
    project_id: int | None = None
    dry_run: bool = False
    run_overrides: dict = field(default_factory=dict)

    def uow(self, summary: str = "", *, op_name: str | None = None) -> UnitOfWork:
        """UnitOfWork với actor/project của context; op name lấy từ `current_op` (do `invoke` đặt) nếu không truyền."""
        from sagareel.core.uow import UnitOfWork  # import muộn: core.uow → core.ops → core.context

        return UnitOfWork(
            self.services.db, actor=self.actor, op_name=op_name, project_id=self.project_id, summary=summary
        )
