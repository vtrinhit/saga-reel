"""Dựng phim: bản dựng, khối dựng tăng dần (fingerprint), gói xuất, phiên upload chia khối."""

from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from sagareel.db.base import Base, JSONDict, JSONList, Plain


class Render(Plain, Base):
    __tablename__ = "render"

    sequence_id: Mapped[int] = mapped_column(ForeignKey("sequence.id"), index=True)
    variant: Mapped[str]  # draft/final/preview
    platform: Mapped[str] = mapped_column(default="")
    lang: Mapped[str] = mapped_column(default="")
    status: Mapped[str] = mapped_column(default="queued")
    fingerprint: Mapped[str] = mapped_column(default="", index=True)
    media_id: Mapped[int | None]
    job_id: Mapped[int | None] = mapped_column(index=True)
    duration_ms: Mapped[int | None]
    meta: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)


class RenderChunk(Plain, Base):
    __tablename__ = "render_chunk"
    __table_args__ = (Index("ix_render_chunk_seq_variant_index", "sequence_id", "variant", "index"),)

    sequence_id: Mapped[int] = mapped_column(ForeignKey("sequence.id"), index=True)
    variant: Mapped[str]
    index: Mapped[int]
    fingerprint: Mapped[str] = mapped_column(index=True)
    media_id: Mapped[int]
    duration_ms: Mapped[int]


class ExportPackage(Plain, Base):
    __tablename__ = "export_package"

    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"), index=True)
    sequence_ids: Mapped[list[Any]] = mapped_column(JSONList, default=list)
    platform: Mapped[str]
    lang: Mapped[str] = mapped_column(default="")
    folder: Mapped[str] = mapped_column(default="")
    checks: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)
    # `metadata` là tên dành riêng của Declarative → thuộc tính `meta_data`, cột vẫn tên `metadata`.
    meta_data: Mapped[dict[str, Any]] = mapped_column("metadata", JSONDict, default=dict)
    status: Mapped[str] = mapped_column(default="pending")


class UploadSession(Plain, Base):
    __tablename__ = "upload_session"

    name: Mapped[str]
    size: Mapped[int]
    kind: Mapped[str]
    received: Mapped[int] = mapped_column(default=0)
    sha256: Mapped[str] = mapped_column(default="")
    path: Mapped[str]
    status: Mapped[str] = mapped_column(default="open")
    media_id: Mapped[int | None]
