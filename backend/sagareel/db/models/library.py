"""Library: loại tài sản, tài sản, look (asset_variant), hồ sơ giọng — spec §6, §23.9."""

from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from sagareel.db.base import Base, JSONDict, JSONList, Tracked


class AssetKind(Tracked, Base):
    """Loại tài sản. `project_id` null = toàn cục (character/location/prop seed sẵn trong migration 0004).

    `key` duy nhất trong các dòng chưa xoá của project; dòng toàn cục có index riêng (SQLite coi NULL là khác nhau).
    """

    __tablename__ = "asset_kind"
    __table_args__ = (
        Index("ux_asset_kind_key", "project_id", "key", unique=True, sqlite_where=text("deleted_at IS NULL")),
        Index("ux_asset_kind_global_key", "key", unique=True, sqlite_where=text("project_id IS NULL AND deleted_at IS NULL")),
    )

    project_id: Mapped[int | None] = mapped_column(ForeignKey("project.id"), index=True)
    key: Mapped[str]
    label: Mapped[str]
    icon: Mapped[str] = mapped_column(default="")
    ref_priority: Mapped[int] = mapped_column(default=0)


class VoiceProfile(Tracked, Base):
    __tablename__ = "voice_profile"
    __table_args__ = (
        Index("ux_voice_profile_key", "project_id", "key", unique=True, sqlite_where=text("deleted_at IS NULL")),
        Index("ux_voice_profile_global_key", "key", unique=True, sqlite_where=text("project_id IS NULL AND deleted_at IS NULL")),
    )

    project_id: Mapped[int | None] = mapped_column(ForeignKey("project.id"), index=True)
    key: Mapped[str]
    name: Mapped[str]
    connection: Mapped[str] = mapped_column(default="")
    voice: Mapped[str] = mapped_column(default="")
    rate: Mapped[str] = mapped_column(default="+0%")
    pitch: Mapped[str] = mapped_column(default="+0Hz")
    volume: Mapped[str] = mapped_column(default="+0%")
    style: Mapped[str] = mapped_column(default="")
    per_lang: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)
    sample_media_id: Mapped[int | None]


class Asset(Tracked, Base):
    """Nhân vật/bối cảnh/đạo cụ… `kind` = `asset_kind.key`. `description` là khối khoá (dán nguyên văn)."""

    __tablename__ = "asset"
    __table_args__ = (Index("ux_asset_key", "project_id", "key", unique=True, sqlite_where=text("deleted_at IS NULL")),)

    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"), index=True)
    kind: Mapped[str]
    key: Mapped[str]
    name: Mapped[str]
    description: Mapped[str] = mapped_column(Text, default="")
    locked: Mapped[bool] = mapped_column(default=False)
    voice_profile_id: Mapped[int | None] = mapped_column(ForeignKey("voice_profile.id"), index=True)
    tags: Mapped[list[Any]] = mapped_column(JSONList, default=list)
    custom: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)
    cover_media_id: Mapped[int | None]


class AssetVariant(Tracked, Base):
    """Look của tài sản (spec §23.9): `lock_word` chèn nguyên văn, `sheet` = `{view: media_id}`."""

    __tablename__ = "asset_variant"
    __table_args__ = (Index("ux_asset_variant_key", "asset_id", "key", unique=True, sqlite_where=text("deleted_at IS NULL")),)

    asset_id: Mapped[int] = mapped_column(ForeignKey("asset.id"), index=True)
    key: Mapped[str]
    name: Mapped[str]
    description_delta: Mapped[str] = mapped_column(Text, default="")
    keep_features: Mapped[list[Any]] = mapped_column(JSONList, default=list)
    lock_word: Mapped[str] = mapped_column(default="")
    anchor_media_id: Mapped[int | None]
    sheet: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)
    ref_media_ids: Mapped[list[Any]] = mapped_column(JSONList, default=list)
    valid_from: Mapped[int | None]
    valid_to: Mapped[int | None]
    is_default: Mapped[bool] = mapped_column(default=False)
