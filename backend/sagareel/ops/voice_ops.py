"""Thao tác nhóm `voice`: hồ sơ giọng (tạo/sửa/liệt kê/xoá hai bước) + nghe thử (job `audio.tts`)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from sagareel.core.context import OpContext
from sagareel.core.ops import op
from sagareel.services import confirm
from sagareel.services.audio import dialogue, voices


class VoiceCreateParams(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    project_id: int | None = Field(default=None, description="Bỏ trống = hồ sơ toàn cục (dùng cho mọi dự án)")
    key: str | None = Field(default=None, description="Mã; bỏ trống = tự sinh từ tên")
    connection: str | None = Field(default=None, description="Tên kết nối TTS; trống = cài đặt tts.connection")
    voice: str | None = Field(default=None, description="Mã giọng của provider, vd. vi-VN-HoaiMyNeural")
    rate: str | None = Field(default=None, description="Tốc độ, dạng +0%")
    pitch: str | None = Field(default=None, description="Cao độ, dạng +0Hz")
    volume: str | None = Field(default=None, description="Âm lượng, dạng +0%")
    style: str | None = None
    per_lang: dict[str, Any] | None = Field(default=None, description="{lang: {connection?, voice?, rate?, ...}}")


class VoiceUpdateParams(BaseModel):
    id: int
    values: dict[str, Any]
    expected_updated_at: datetime | None = None


class VoiceListParams(BaseModel):
    project_id: int | None = None
    include_global: bool = True


class IdsParams(BaseModel):
    ids: list[int] = Field(min_length=1)


class DeleteParams(BaseModel):
    confirm_token: str | None = Field(default=None, description="Lấy từ <loại>.delete_preview")


class DeletePreviewOut(BaseModel):
    impact: dict[str, Any]
    confirm_token: str
    expires_in_s: int = confirm.TTL_S


class VoicePreviewParams(BaseModel):
    text: str = Field(min_length=1, max_length=2000, description="Câu nghe thử (gửi nguyên văn)")
    voice_profile_id: int | None = None
    lang: str | None = None
    connection: str | None = Field(default=None, description="Ghi đè kết nối")
    voice: str | None = Field(default=None, description="Ghi đè giọng")


@op("voice.create", summary="Tạo hồ sơ giọng (kết nối TTS, giọng, tốc độ/cao độ/âm lượng, giọng theo ngôn ngữ)",
    params=VoiceCreateParams, mcp="tool")
def voice_create(ctx: OpContext, p: VoiceCreateParams) -> dict:
    vals = p.model_dump(exclude={"name", "project_id", "key"})
    return voices.create(ctx, p.name, project_id=p.project_id, key=p.key, **vals)


@op("voice.update", summary="Sửa hồ sơ giọng (hoàn tác được)", params=VoiceUpdateParams, idempotent=True, mcp="tool")
def voice_update(ctx: OpContext, p: VoiceUpdateParams) -> dict:
    return voices.update(ctx, p.id, p.values, p.expected_updated_at)


@op("voice.list", summary="Liệt kê hồ sơ giọng của dự án (+ toàn cục)", params=VoiceListParams, read_only=True,
    idempotent=True, mcp="tool")
def voice_list(ctx: OpContext, p: VoiceListParams) -> dict:
    return {"items": voices.list_profiles(ctx, p.project_id, p.include_global)}


@op("voice.delete_preview", summary="Xem tác động khi xoá hồ sơ giọng + confirm_token", params=IdsParams,
    result=DeletePreviewOut, read_only=True, idempotent=True, viewer=False)
def voice_delete_preview(ctx: OpContext, p: IdsParams) -> dict:
    from sagareel.services import entities

    return entities.soft_delete_preview(ctx, "voice_profile", p.ids)


@op("voice.delete", summary="Xoá (mềm) hồ sơ giọng — cần confirm_token", params=DeleteParams, destructive=True)
def voice_delete(ctx: OpContext, p: DeleteParams) -> dict:
    from sagareel.services import entities

    return entities.soft_delete(ctx, p.confirm_token, expect_type="voice_profile")


@op("voice.preview", summary="Nghe thử một câu với hồ sơ giọng (job audio.tts, trả job_id)",
    params=VoicePreviewParams, long_running=True, mcp="tool")
def voice_preview(ctx: OpContext, p: VoicePreviewParams) -> dict:
    return dialogue.preview(ctx, p.text, voice_profile_id=p.voice_profile_id, lang=p.lang, connection=p.connection,
                            voice=p.voice)
