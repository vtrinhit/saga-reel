"""Sequence (tập), scene, clip, lời thoại — spec §6, §23.1, §23.9. Thứ tự theo `rank` (fractional index)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from sagareel.db.base import Base, JSONDict, JSONList, Tracked, new_key


class Sequence(Tracked, Base):
    __tablename__ = "sequence"
    __table_args__ = (Index("ux_sequence_key", "project_id", "key", unique=True, sqlite_where=text("deleted_at IS NULL")),)

    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"), index=True)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("group_node.id"), index=True)
    key: Mapped[str]
    number: Mapped[int] = mapped_column(default=1)
    title: Mapped[str]
    synopsis: Mapped[str] = mapped_column(Text, default="")
    script: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(default="draft")
    workflow_key: Mapped[str] = mapped_column(default="")
    gates: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)
    pipeline: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)  # trạng thái từng bước quy trình
    target_platforms: Mapped[list[Any]] = mapped_column(JSONList, default=list)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)


class Scene(Tracked, Base):
    __tablename__ = "scene"
    __table_args__ = (
        Index("ux_scene_key", "sequence_id", "key", unique=True, sqlite_where=text("deleted_at IS NULL")),
        Index("ix_scene_sequence_rank", "sequence_id", "rank"),
    )

    sequence_id: Mapped[int] = mapped_column(ForeignKey("sequence.id"), index=True)
    key: Mapped[str]
    rank: Mapped[str]
    title: Mapped[str] = mapped_column(default="")
    location_asset_id: Mapped[int | None] = mapped_column(ForeignKey("asset.id"), index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    # time_of_day, era, weather, lighting, char_states
    state: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)


class Clip(Tracked, Base):
    __tablename__ = "clip"
    __table_args__ = (
        Index("ux_clip_key", "sequence_id", "key", unique=True, sqlite_where=text("deleted_at IS NULL")),
        Index("ix_clip_sequence_rank", "sequence_id", "rank"),
    )

    sequence_id: Mapped[int] = mapped_column(ForeignKey("sequence.id"), index=True)
    scene_id: Mapped[int | None] = mapped_column(ForeignKey("scene.id"), index=True)
    rank: Mapped[str]
    key: Mapped[str]
    title: Mapped[str] = mapped_column(default="")
    image_prompt: Mapped[str] = mapped_column(Text, default="")
    image_prompt_src: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)
    motion_prompt: Mapped[str] = mapped_column(Text, default="")
    motion_prompt_src: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)
    prompt_mode: Mapped[str] = mapped_column(default="synced")  # verbatim/synced
    assets: Mapped[list[Any]] = mapped_column(JSONList, default=list)  # [{asset_id, variant_id?, role?}]
    extra_ref_media_ids: Mapped[list[Any]] = mapped_column(JSONList, default=list)
    input_media_id: Mapped[int | None]
    engine: Mapped[str] = mapped_column(default="ai_video")  # ai_video/still_motion/code_anim
    motion_preset: Mapped[str] = mapped_column(default="")
    duration_ms: Mapped[int] = mapped_column(default=6000)
    link: Mapped[str] = mapped_column(default="cut")  # cut/fade/continue
    transition: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)
    trim_in_ms: Mapped[int] = mapped_column(default=0)
    trim_out_ms: Mapped[int] = mapped_column(default=0)
    speed: Mapped[float] = mapped_column(default=1.0)
    grok_audio: Mapped[str] = mapped_column(default="ambient")  # keep/mute/ambient
    subtitle_mode: Mapped[str] = mapped_column(default="auto")
    image_take_id: Mapped[int | None]
    video_take_id: Mapped[int | None]
    flags: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)  # stale_image, stale_video, drift…
    review: Mapped[str] = mapped_column(default="pending")  # pending/approved/rejected/skipped
    notes: Mapped[str] = mapped_column(Text, default="")
    shot_size: Mapped[str] = mapped_column(default="")
    angle: Mapped[str] = mapped_column(default="")
    camera_move: Mapped[str] = mapped_column(default="")
    lens: Mapped[str] = mapped_column(default="")
    composition: Mapped[str] = mapped_column(default="")
    eyeline: Mapped[str] = mapped_column(default="")
    screen_dir: Mapped[str] = mapped_column(default="")
    first_frame_desc: Mapped[str] = mapped_column(Text, default="")
    last_frame_desc: Mapped[str] = mapped_column(Text, default="")
    sfx_desc: Mapped[str] = mapped_column(Text, default="")
    beats: Mapped[list[Any]] = mapped_column(JSONList, default=list)
    axis_side: Mapped[str] = mapped_column(default="")  # left/right — phía trục 180°
    cut_in: Mapped[str] = mapped_column(default="")  # kiểu cắt vào (match_action, cutaway…)
    cut_out: Mapped[str] = mapped_column(default="")
    connection: Mapped[str] = mapped_column(default="")  # kết nối provider riêng của clip (trống = cài đặt)
    model: Mapped[str] = mapped_column(default="")
    custom: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)


class DialogueLine(Tracked, Base):
    __tablename__ = "dialogue_line"
    __table_args__ = (
        Index("ix_dialogue_line_clip_rank", "clip_id", "rank"),
        Index("ux_dialogue_line_key", "clip_id", "key", unique=True, sqlite_where=text("deleted_at IS NULL")),
    )

    clip_id: Mapped[int] = mapped_column(ForeignKey("clip.id"), index=True)
    key: Mapped[str] = mapped_column(default=lambda: new_key("l"))  # ổn định cho plan/apply
    rank: Mapped[str]
    speaker_asset_id: Mapped[int | None] = mapped_column(ForeignKey("asset.id"), index=True)  # null = người kể
    text: Mapped[str] = mapped_column(Text)
    emotion: Mapped[str] = mapped_column(default="")
    source: Mapped[str] = mapped_column(default="tts")  # tts/upload/grok/none
    voice_override: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)
    audio_take_id: Mapped[int | None]
    offset_ms: Mapped[int] = mapped_column(default=0)
    subtitle: Mapped[bool] = mapped_column(default=True)
    translations: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)
