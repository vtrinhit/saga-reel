"""Thao tác nhóm `render`: ước tính, dựng (job `render.sequence`), danh sách bản dựng, cache dựng phim."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from sagareel.core.context import OpContext
from sagareel.core.errors import NotFound
from sagareel.core.ops import op
from sagareel.db.models import Media, Render, RenderChunk, Sequence
from sagareel.render import cache, pipeline
from sagareel.services import confirm, jobs

Variant = Literal["draft", "animatic", "final"]
Scope = Literal["segments", "chunks", "work", "all"]


class RenderEstimateParams(BaseModel):
    sequence_id: int
    variant: Variant = Field("draft", description="draft (540p nhanh) / animatic (ảnh + still_motion) / final")
    platform: str | None = Field(None, description="Preset nền tảng (yt-long dùng hồ sơ mã hoá riêng)")
    lang: str | None = Field(None, description="Ngôn ngữ thoại/phụ đề; bỏ trống = ngôn ngữ chính của project")
    preview_seconds: float | None = Field(None, gt=0, description="Chỉ dựng N giây đầu (xem trước có tiếng)")


class RenderEstimateOut(BaseModel):
    sequence_id: int
    variant: str
    clips: int
    videos: int
    stills: int
    missing: int
    missing_clips: list[dict[str, Any]]
    seconds: float
    chunks: int
    chunks_cached: int
    est_run_seconds: float
    est_disk_bytes: int
    width: int
    height: int
    warnings: list[dict[str, Any]]


class RenderStartParams(RenderEstimateParams):
    priority: int = Field(0, description="Ưu tiên job (cao chạy trước)")


class RenderListParams(BaseModel):
    sequence_id: int
    limit: int = Field(20, ge=1, le=200)


class RenderOut(BaseModel):
    id: int
    sequence_id: int
    variant: str
    platform: str
    lang: str
    status: str
    fingerprint: str
    media_id: int | None
    job_id: int | None
    duration_ms: int | None
    meta: dict[str, Any]
    url: str | None
    created_at: str | None


class RenderListOut(BaseModel):
    items: list[RenderOut]


class CacheStatsParams(BaseModel):
    pass


class CacheScopeParams(BaseModel):
    scope: Scope = Field("all", description="segments / chunks / work / all")


class CacheClearParams(CacheScopeParams):
    confirm_token: str | None = Field(None, description="Lấy từ render.cache_clear_preview")


def _seq_project(ctx: OpContext, sequence_id: int) -> int:
    with ctx.services.db.session() as s:
        seq = s.get(Sequence, sequence_id)
        if seq is None or seq.deleted_at is not None:
            raise NotFound(f"Không tìm thấy tập #{sequence_id}.")
        return seq.project_id


@op("render.estimate", summary="Ước tính trước khi dựng: số clip/khối, khối đã có, giây phim, thời gian chạy, dung lượng",
    params=RenderEstimateParams, result=RenderEstimateOut, read_only=True, idempotent=True, mcp="tool")
def render_estimate(ctx: OpContext, p: RenderEstimateParams) -> dict:
    return pipeline.estimate(ctx.services, p.sequence_id, p.variant, p.platform, p.lang, p.preview_seconds)


@op("render.start", summary="Dựng tập (job render.sequence, tiếp tục theo khối; khối không đổi không dựng lại)",
    params=RenderStartParams, long_running=True, mcp="tool")
def render_start(ctx: OpContext, p: RenderStartParams) -> dict:
    project_id = _seq_project(ctx, p.sequence_id)
    params = {"sequence_id": p.sequence_id, "variant": p.variant, "platform": p.platform, "lang": p.lang,
              "preview_seconds": p.preview_seconds}
    ref = f"render:{p.sequence_id}:{p.variant}:{p.platform or ''}:{p.lang or ''}:{p.preview_seconds or ''}"
    return jobs.submit(ctx.services, pipeline.JOB_TYPE, params, lane=pipeline.LANE, priority=p.priority,
                       project_id=project_id, sequence_id=p.sequence_id, client_ref=ref, max_attempts=2)


@op("render.list", summary="Các bản dựng của một tập (mới nhất trước) kèm trạng thái, file, cảnh báo",
    params=RenderListParams, result=RenderListOut, read_only=True, idempotent=True, mcp="tool")
def render_list(ctx: OpContext, p: RenderListParams) -> dict:
    with ctx.services.db.session() as s:
        rows = s.scalars(select(Render).where(Render.sequence_id == p.sequence_id)
                         .order_by(Render.id.desc()).limit(p.limit)).all()
        return {"items": [pipeline.render_dict(r) for r in rows]}


@op("render.cache_stats", summary="Dung lượng cache dựng phim (segment, khối, file tạm)", params=CacheStatsParams,
    read_only=True, idempotent=True, mcp="tool")
def render_cache_stats(ctx: OpContext, p: CacheStatsParams) -> dict:
    out = cache.stats(ctx.services.settings.cache_dir)
    with ctx.services.db.session() as s:
        out["chunk_rows"] = len(s.scalars(select(RenderChunk.id)).all())
    return out


def _impact(ctx: OpContext, scope: str) -> dict:
    st = cache.stats(ctx.services.settings.cache_dir)
    keys = ("segments", "chunks", "work") if scope == "all" else (scope,)
    return {"scope": scope, "files": sum(st[k]["files"] for k in keys), "bytes": sum(st[k]["bytes"] for k in keys)}


@op("render.cache_clear_preview", summary="Xem dung lượng sẽ giải phóng khi xoá cache dựng phim, lấy confirm_token",
    params=CacheScopeParams, read_only=True, idempotent=True, viewer=False, mcp="tool")
def render_cache_clear_preview(ctx: OpContext, p: CacheScopeParams) -> dict:
    with ctx.services.db.session() as s:
        token = confirm.make_token(s, "render.cache_clear", [], {"scope": p.scope})
        s.commit()
    return {"impact": _impact(ctx, p.scope), "confirm_token": token, "expires_in_s": confirm.TTL_S}


@op("render.cache_clear", summary="Xoá cache dựng phim (luôn tạo lại được) — cần confirm_token từ preview",
    params=CacheClearParams, destructive=True, mcp="tool")
def render_cache_clear(ctx: OpContext, p: CacheClearParams) -> dict:
    with ctx.services.db.session() as s:
        confirm.verify(s, "render.cache_clear", [], {"scope": p.scope}, p.confirm_token)
    root = ctx.services.settings.cache_dir
    out = cache.clear(root, p.scope)
    if p.scope in ("chunks", "all"):
        # Khối là file phái sinh: bỏ dòng render_chunk và bản ghi media phái sinh trong cache.
        prefix = root.resolve().relative_to(ctx.services.settings.data_dir.resolve()).as_posix() + "/render/"
        with ctx.services.db.session(immediate=True) as s:
            s.execute(delete(RenderChunk))
            s.execute(delete(Media).where(Media.origin == "derived", Media.path.like(prefix + "%")))
            s.commit()
    return {"scope": p.scope, **out}
