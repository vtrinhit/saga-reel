"""Thao tác nhóm `project`: tạo, đọc (kèm số đếm), liệt kê, sửa (chống ghi đè), xoá mềm hai bước, nhân bản."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from sagareel.core.context import OpContext
from sagareel.core.ops import op
from sagareel.services import confirm, projects


class ProjectCounts(BaseModel):
    assets: int
    sequences: int
    clips: int
    takes_pending: int = Field(0, description="Clip chờ duyệt đã có take (= review.queue review=pending + has_take)")
    drift: int = Field(0, description="Clip mang cờ drift")


class ProjectOut(BaseModel):
    id: int
    key: str
    name: str
    description: str
    genre_key: str
    genre: dict[str, Any] = Field(default_factory=dict, description="Số liệu thể loại của dự án")
    workflows: dict[str, Any] = Field(default_factory=dict, description="Quy trình của dự án {key: {steps…}}")
    narrator_voice: str = ""
    is_sample: bool = False
    aspect: str
    resolution: str
    fps: int
    language: str
    languages: list[Any]
    target_platforms: list[Any]
    style_block: str
    negative_block: str
    settings: dict[str, Any]
    cover_media_id: int | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
    counts: ProjectCounts


class ProjectPage(BaseModel):
    items: list[ProjectOut]
    next_cursor: int | None = None


class ProjectCreateParams(BaseModel):
    name: str = Field(min_length=1, max_length=200, description="Tên dự án (tiếng Việt có dấu được)")
    key: str | None = Field(default=None, description="Mã duy nhất (không dấu); bỏ trống = tự sinh từ tên")
    genre_key: str | None = Field(default=None, description="Nhãn thể loại (series, serial, narration, montage, "
                                  "storyboard…); dự án trống chép số liệu tham khảo của thể loại này")
    aspect: str | None = Field(default=None, description="Tỉ lệ khung, mặc định 9:16")
    language: str | None = Field(default=None, description="Ngôn ngữ chính, mặc định vi")
    target_platforms: list[str] | None = Field(default=None, description="Nền tảng đích (preset platform)")
    from_project_id: int | None = Field(default=None, description="Nhân bản từ dự án này (thường là dự án mẫu): "
                                        "prompt, phong cách, số liệu, quy trình. Bỏ trống = dự án trống (chưa có prompt)")
    include: dict[str, bool] | None = Field(default=None, description="Khi nhân bản: phần chép {prompts, style, "
                                            "settings, library} (mặc định tất cả)")


class ProjectIdParams(BaseModel):
    id: int


class ProjectListParams(BaseModel):
    cursor: int | None = Field(default=None, description="`next_cursor` của trang trước")
    limit: int = Field(default=50, ge=1, le=200)


class ProjectUpdateParams(BaseModel):
    id: int
    values: dict[str, Any] = Field(description="Cột → giá trị mới, vd. {\"name\": \"…\", \"style_block\": \"…\"}")
    expected_updated_at: datetime | None = Field(
        default=None, description="`updated_at` lúc đọc; lệch (người khác đã sửa) → lỗi stale_write 409"
    )


class DeletePreviewOut(BaseModel):
    impact: dict[str, Any]
    confirm_token: str
    expires_in_s: int = confirm.TTL_S


class ProjectDeleteParams(BaseModel):
    confirm_token: str | None = Field(default=None, description="Lấy từ project.delete_preview")


class ProjectDuplicateParams(BaseModel):
    id: int
    name: str = Field(min_length=1, max_length=200, description="Tên dự án mới")
    key: str | None = Field(default=None, description="Mã dự án mới; bỏ trống = tự sinh từ tên")
    include: dict[str, bool] | None = Field(default=None, description="Phần chép {prompts, style, settings, library} "
                                            "(mặc định tất cả)")


@op("project.create", summary="Tạo dự án mới: trống hoặc nhân bản từ dự án mẫu (from_project_id)",
    params=ProjectCreateParams,
    result=ProjectOut, mcp="tool")
def project_create(ctx: OpContext, p: ProjectCreateParams) -> dict:
    return projects.create(ctx, p.name, key=p.key, genre_key=p.genre_key, aspect=p.aspect, language=p.language,
                           target_platforms=p.target_platforms, from_project_id=p.from_project_id, include=p.include)


@op("project.get", summary="Đọc một dự án kèm số tài sản / tập / clip", params=ProjectIdParams, result=ProjectOut,
    read_only=True, idempotent=True, mcp="tool")
def project_get(ctx: OpContext, p: ProjectIdParams) -> dict:
    return projects.get(ctx, p.id)


@op("project.list", summary="Liệt kê dự án (mới nhất trước), phân trang cursor", params=ProjectListParams,
    result=ProjectPage, read_only=True, idempotent=True, mcp="tool")
def project_list(ctx: OpContext, p: ProjectListParams) -> dict:
    return projects.list_projects(ctx, p.cursor, p.limit)


@op("project.update", summary="Sửa dự án (hoàn tác được); kèm expected_updated_at để chống ghi đè",
    params=ProjectUpdateParams, result=ProjectOut, idempotent=True, mcp="tool")
def project_update(ctx: OpContext, p: ProjectUpdateParams) -> dict:
    return projects.update(ctx, p.id, p.values, p.expected_updated_at)


@op("project.delete_preview", summary="Xem tác động của việc xoá dự án và lấy confirm_token (10 phút)",
    params=ProjectIdParams, result=DeletePreviewOut, read_only=True, idempotent=True, viewer=False, mcp="tool")
def project_delete_preview(ctx: OpContext, p: ProjectIdParams) -> dict:
    return projects.delete_preview(ctx, p.id)


@op("project.delete", summary="Xoá (mềm, hoàn tác được) dự án — cần confirm_token từ project.delete_preview",
    params=ProjectDeleteParams, destructive=True, mcp="tool")
def project_delete(ctx: OpContext, p: ProjectDeleteParams) -> dict:
    return projects.delete(ctx, p.confirm_token)


@op("project.duplicate", summary="Nhân bản dự án (vd. từ dự án mẫu): prompt + phong cách + cài đặt/quy trình + Library "
    "tuỳ chọn (không chép tập/clip/take)",
    params=ProjectDuplicateParams, result=ProjectOut, mcp="tool")
def project_duplicate(ctx: OpContext, p: ProjectDuplicateParams) -> dict:
    return projects.duplicate(ctx, p.id, p.name, key=p.key, include=p.include)


class SampleReseedParams(BaseModel):
    keys: list[str] | None = Field(default=None, description="Key/slug dự án mẫu cần tạo lại; bỏ trống = mọi mẫu thiếu")


@op("sample.reseed", summary="Tạo lại dự án mẫu cài sẵn đã xoá/thiếu (mẫu đang có giữ nguyên)",
    params=SampleReseedParams, mcp="tool")
def sample_reseed(ctx: OpContext, p: SampleReseedParams) -> dict:
    from sagareel.services import samples

    return samples.reseed(ctx, p.keys)


class EmptyParams(BaseModel):
    pass


@op("genre.list", summary="Số liệu thể loại tham khảo (độ dài tập/clip, số clip, hook…) — chép vào project.genre",
    params=EmptyParams, read_only=True, idempotent=True, viewer=True, mcp="tool")
def genre_list(ctx: OpContext, p: EmptyParams) -> dict:
    from sagareel.services import genres

    return {"items": genres.references()}


@op("workflow.defaults", summary="Quy trình mặc định của tool (chép vào project.workflows khi tạo dự án)",
    params=EmptyParams, read_only=True, idempotent=True, viewer=True, mcp="tool")
def workflow_defaults(ctx: OpContext, p: EmptyParams) -> dict:
    from sagareel.services.pipeline import workflow as W

    return {"items": [{"key": k, "name": v.get("name", k), "data": v} for k, v in W.defaults().items()]}
