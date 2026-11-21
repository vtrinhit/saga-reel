"""Project và cây nhóm (series/season/arc) — spec §6."""

from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from sagareel.db.base import Base, JSONDict, JSONList, Tracked


def _default_languages() -> list[str]:
    return ["vi"]


class Project(Tracked, Base):
    __tablename__ = "project"
    __table_args__ = (Index("ux_project_key", "key", unique=True, sqlite_where=text("deleted_at IS NULL")),)

    key: Mapped[str] = mapped_column(String(collation="NOCASE"))  # không phân biệt hoa thường (migration 0008)
    name: Mapped[str]
    description: Mapped[str] = mapped_column(Text, default="")
    genre_key: Mapped[str] = mapped_column(default="series")  # nhãn thể loại (tự do), số liệu nằm ở `genre`
    # Số liệu thể loại của project (độ dài tập/clip, số clip, chuyển cảnh, hook, kiểu kết… — `presets.GenreData`).
    genre: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)
    # Quy trình của project: {workflow_key: {label, steps, gates, batch…}} (`pipeline.workflow`).
    workflows: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)
    narrator_voice: Mapped[str] = mapped_column(Text, default="")  # giọng người kể (voice-over của video AI)
    is_sample: Mapped[bool] = mapped_column(default=False)  # dự án mẫu: nhân bản ra dự án mới
    aspect: Mapped[str] = mapped_column(default="9:16")
    resolution: Mapped[str] = mapped_column(default="1080p")
    fps: Mapped[int] = mapped_column(default=30)
    language: Mapped[str] = mapped_column(default="vi")
    languages: Mapped[list[Any]] = mapped_column(JSONList, default=_default_languages)
    target_platforms: Mapped[list[Any]] = mapped_column(JSONList, default=list)
    style_block: Mapped[str] = mapped_column(Text, default="")
    negative_block: Mapped[str] = mapped_column(Text, default="")
    settings: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)
    cover_media_id: Mapped[int | None]


class ProjectPrompt(Tracked, Base):
    """Prompt của project (spec §8): thân Jinja cho `key` (vd. `image.synced`, `story_write_script`). Hợp đồng biến
    của từng khoá ở `compile/prompt_contracts.py`; không có prompt chung toàn tool."""

    __tablename__ = "project_prompt"
    __table_args__ = (
        Index("ux_project_prompt_key", "project_id", "key", unique=True, sqlite_where=text("deleted_at IS NULL")),
    )

    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"), index=True)
    key: Mapped[str]
    body: Mapped[str] = mapped_column(Text, default="")


class GroupNode(Tracked, Base):
    """Nhóm tập: series → season → arc (cây qua `parent_id`)."""

    __tablename__ = "group_node"
    __table_args__ = (
        Index("ux_group_node_key", "project_id", "key", unique=True, sqlite_where=text("deleted_at IS NULL")),
        Index("ix_group_node_project_rank", "project_id", "rank"),
    )

    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"), index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("group_node.id"), index=True)
    kind: Mapped[str]  # series/season/arc
    key: Mapped[str]
    title: Mapped[str]
    rank: Mapped[str]
    synopsis: Mapped[str] = mapped_column(Text, default="")
