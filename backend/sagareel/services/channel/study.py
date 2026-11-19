"""Nghiên cứu kênh mẫu (bảng `channel_study`): tạo/đọc/liệt kê/xoá hai bước. Chỉ nhận video đã upload (không tải URL)."""

from __future__ import annotations

from typing import Any

from sagareel.container import Services
from sagareel.core.context import OpContext
from sagareel.core.errors import AppError, NotFound, ValidationFailed
from sagareel.db.models import ChannelStudy
from sagareel.services import entities, media

TYPE = "channel_study"
MAX_VIDEOS = 20
SYSTEM_ACTOR = "system:channel"


def system_ctx(services: Services) -> OpContext:
    return OpContext(services=services, actor=SYSTEM_ACTOR, role="owner")


def create(ctx: OpContext, title: str, media_ids: list[int], captions: str = "",
           project_id: int | None = None) -> dict[str, Any]:
    title = title.strip()
    if not title:
        raise ValidationFailed("Tên phân tích không được để trống.")
    ids = list(dict.fromkeys(media_ids))
    if not ids:
        raise ValidationFailed("Cần ít nhất một video mẫu (đã upload).", code="study_media_required")
    if len(ids) > MAX_VIDEOS:
        raise ValidationFailed(f"Mỗi phân tích tối đa {MAX_VIDEOS} video mẫu.", code="study_too_many_videos")
    u = entities.uow(ctx, f"Tạo phân tích kênh '{title}'", "channel.study_create")
    with u as s:
        if project_id is not None:
            entities.get(s, "project", project_id)
        for mid in ids:
            m = media.get(s, mid)
            if m.kind != "video":
                raise AppError("media_kind_mismatch", f"Media #{mid} là {m.kind}, cần video.", status=422)
        u.project_id = project_id
        row = ChannelStudy(project_id=project_id, title=title, source="upload", media_ids=ids,
                           captions=captions or "", status="pending", result={})
        s.add(row)
        s.flush()
        return entities.to_dict(row)


def get(ctx: OpContext, study_id: int) -> dict[str, Any]:
    with ctx.services.db.session() as s:
        return entities.to_dict(entities.get(s, TYPE, study_id))


def list_page(ctx: OpContext, project_id: int | None, cursor: str | None, limit: int) -> dict[str, Any]:
    flt = {"project_id": project_id} if project_id is not None else None
    with ctx.services.db.session() as s:
        page = entities.list_page(s, TYPE, flt, cursor, limit)
    for it in page["items"]:  # danh sách gọn: chỉ kèm phần nhịp của kết quả
        res = it.get("result") or {}
        it["result"] = {"pacing": (res.get("aggregate") or {}).get("pacing")} if res else {}
    return page


def delete_preview(ctx: OpContext, study_id: int) -> dict[str, Any]:
    return entities.soft_delete_preview(ctx, TYPE, [study_id])


def delete(ctx: OpContext, confirm_token: str | None) -> dict[str, Any]:
    return entities.soft_delete(ctx, confirm_token, expect_type=TYPE)


def set_state(services: Services, study_id: int, status: str, result: dict[str, Any] | None = None) -> None:
    ctx = system_ctx(services)
    u = entities.uow(ctx, f"Phân tích kênh #{study_id}: {status}", "channel.analyze")
    with u as s:
        row = s.get(ChannelStudy, study_id)
        if row is None or row.deleted_at is not None:
            raise NotFound(f"Không tìm thấy phân tích kênh #{study_id}.")
        u.project_id = row.project_id
        row.status = status
        if result is not None:
            row.result = result
