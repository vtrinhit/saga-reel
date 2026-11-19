"""Thao tác nhóm `audio`: đọc TTS lời thoại (job `audio.tts` mỗi câu, có cache), khớp thời lượng giọng ↔ clip,
xem trước phụ đề."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from sagareel.core.context import OpContext
from sagareel.core.errors import ValidationFailed
from sagareel.core.ops import op
from sagareel.services.audio import dialogue, subtitles

SyncMode = Literal["voice_drives", "clip_drives", "none"]


class AudioGenerateParams(BaseModel):
    clip_ids: list[int] | None = Field(default=None, description="Các clip; trống thì dùng sequence_id")
    sequence_id: int | None = None
    lang: str | None = Field(default=None, description="Ngôn ngữ (bản dịch trong translations); trống = ngôn ngữ chính")
    force: bool = Field(default=False, description="Bỏ qua cache, đọc lại")
    dry_run: bool = False


class DurationPlanParams(BaseModel):
    sequence_id: int
    clip_ids: list[int] | None = None
    mode: SyncMode | None = Field(default=None, description="Trống = cài đặt audio.duration_sync")


class SubtitlePreviewParams(BaseModel):
    text: str = Field(min_length=1)
    duration_ms: int = Field(default=3000, gt=0)
    style: str = Field(default="9x16", description="Preset subtitle_style: 9x16 / 9x16_box / 16x9")
    mode: Literal["outline", "karaoke_fill", "word_pop"] | None = None
    safe_zone: dict[str, int] | None = None


@op("audio.generate", summary="Đọc TTS lời thoại của clip/tập (job audio.tts mỗi câu, cache theo giọng)",
    params=AudioGenerateParams, costly=True, long_running=True, mcp="tool")
def audio_generate(ctx: OpContext, p: AudioGenerateParams) -> dict:
    if p.clip_ids is None and p.sequence_id is None:
        raise ValidationFailed("Cần clip_ids hoặc sequence_id.")
    return dialogue.generate(ctx, clip_ids=p.clip_ids, sequence_id=p.sequence_id, lang=p.lang, dry_run=p.dry_run,
                             force=p.force)


@op("audio.duration_plan", summary="Đề xuất thời lượng clip theo giọng (voice_drives/clip_drives/none)",
    params=DurationPlanParams, read_only=True, idempotent=True, mcp="tool")
def audio_duration_plan(ctx: OpContext, p: DurationPlanParams) -> dict:
    return dialogue.duration_plan(ctx, p.sequence_id, p.clip_ids, p.mode)


@op("audio.apply_duration_plan", summary="Áp đề xuất thời lượng giọng ↔ clip (một change set, hoàn tác được)",
    params=DurationPlanParams, idempotent=True, mcp="tool")
def audio_apply_duration_plan(ctx: OpContext, p: DurationPlanParams) -> dict:
    return dialogue.apply_duration_plan(ctx, p.sequence_id, p.clip_ids, p.mode)


@op("audio.subtitle_preview", summary="Xem trước cách chia cụm/ngắt dòng phụ đề (ASS + SRT) cho một câu",
    params=SubtitlePreviewParams, read_only=True, idempotent=True)
def audio_subtitle_preview(ctx: OpContext, p: SubtitlePreviewParams) -> dict:
    try:
        st = subtitles.load_style(p.style, mode=p.mode)
    except KeyError:
        raise ValidationFailed(f"Không có preset phụ đề '{p.style}'.",
                               hint=f"Chọn một trong: {', '.join(subtitles.builtin_keys())}.") from None
    cues = subtitles.build_cues([{"text": p.text, "start_ms": 0, "duration_ms": p.duration_ms}], st,
                                safe_zone=p.safe_zone)
    return {"cues": [{"start_ms": c.start_ms, "end_ms": c.end_ms, "text": c.text, "warnings": c.warnings}
                     for c in cues],
            "ass": subtitles.to_ass(cues, st), "srt": subtitles.to_srt(cues),
            "safe_zone_violations": subtitles.safe_zone_violations(st, p.safe_zone) if p.safe_zone else []}
