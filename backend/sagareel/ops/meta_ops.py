"""Thao tác nhóm `meta`: danh mục giá trị hợp lệ (enum) — một nguồn cho validator backend và UI."""

from __future__ import annotations

from pydantic import BaseModel

from sagareel.core.context import OpContext
from sagareel.core.ops import op


class MetaEnumsParams(BaseModel):
    pass


def enums() -> dict:
    from sagareel.services import clips, sequences, takes

    return {
        "shot": {k: list(v) for k, v in clips.SHOT_ENUMS.items()},
        "cut": {k: list(v) for k, v in clips.CUT_ENUMS.items()},
        "screen_dir_aliases": dict(clips.SCREEN_DIR_ALIASES),
        "clip": {k: list(v) for k, v in clips.FIELD_ENUMS.items()},
        "engines": list(clips.FIELD_ENUMS["engine"]),
        "links": list(clips.FIELD_ENUMS["link"]),
        "review": list(clips.FIELD_ENUMS["review"]),
        "verdicts": list(takes.VERDICTS),
        "take_parts": ["image", "video", "audio", "sheet"],
        "take_statuses": ["queued", "running", "succeeded", "failed", "cancelled"],
        "regenerate_parts": list(takes.PARTS),
        "sequence_statuses": sorted(sequences.SEQUENCE_STATUSES),
        "clip_flags": ["stale_image", "stale_video", "stale_audio", "stale_subtitle", "stale_chunk", "drift", "failed"],
    }


@op("meta.enums", summary="Danh mục enum: cỡ cảnh/góc/chuyển động máy…, engine, kiểu nối, duyệt, phán quyết take",
    params=MetaEnumsParams, read_only=True, idempotent=True, mcp="tool")
def meta_enums(ctx: OpContext, p: MetaEnumsParams) -> dict:
    return enums()
