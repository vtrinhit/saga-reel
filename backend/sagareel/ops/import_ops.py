"""Thao tác nhóm `import`: nhập clip hàng loạt qua xem trước → áp (spec §14 mục 2; định dạng ở `docs/import-format.md`)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from sagareel.core.context import OpContext
from sagareel.core.ops import op
from sagareel.services import importer


class ImportSource(BaseModel):
    kind: Literal["lines", "csv", "xlsx", "json", "yaml", "folder"] = Field(
        description="lines = dán mỗi dòng một clip; csv/xlsx/json/yaml = bảng/tệp có cột; folder = mỗi ảnh một clip"
    )
    text: str | None = Field(None, description="Nội dung dán (lines/csv/json/yaml)")
    upload_id: str | None = Field(None, description="Id upload chia khối (kind `other`) chứa file csv/xlsx/json/yaml")
    media_ids: list[int] | None = Field(None, description="folder: id các media ảnh (mỗi ảnh một clip)")
    sheet: str | None = Field(None, description="xlsx: tên sheet (mặc định sheet đang mở)")

    @model_validator(mode="after")
    def _one_source(self) -> "ImportSource":
        if self.kind == "folder" and not self.media_ids:
            raise ValueError("folder cần `media_ids`")
        if self.kind != "folder" and self.text is None and self.upload_id is None:
            raise ValueError("cần `text` hoặc `upload_id`")
        return self


class ImportPreviewParams(BaseModel):
    sequence_id: int = Field(description="Tập sẽ nhận clip")
    source: ImportSource
    mapping: dict[str, str | None] | None = Field(
        None, description="Ánh xạ thủ công {tên cột: trường chuẩn | null (bỏ qua)}; mặc định tự đoán theo bí danh Việt/Anh"
    )


class RowEdit(BaseModel):
    index: int = Field(description="`index` của hàng trong bản xem trước")
    fields: dict[str, Any] = Field(description="Trường chuẩn → giá trị thô mới (vd. {\"duration\": \"6\", \"characters\": \"AN, BI:áo_đỏ\"})")


class ImportApplyParams(BaseModel):
    sequence_id: int
    preview_token: str = Field(description="Mã từ `import.preview` (sống 1 giờ, dùng một lần)")
    edits: list[RowEdit] | None = Field(None, description="Sửa tay từng hàng trước khi áp")
    create_assets: bool = Field(False, description="Tạo asset nhân vật còn thiếu theo mã; false = bỏ qua mã đó (cảnh báo)")
    after_clip_id: int | None = Field(None, description="Chèn ngay sau clip này; bỏ trống = nối cuối tập")
    skip_rows: list[int] | None = Field(None, description="`index` các hàng không nhập")
    skip_errors: bool = Field(False, description="true = bỏ qua hàng còn lỗi; false = còn hàng lỗi thì từ chối cả lô")


class ImportPreviewResult(BaseModel):
    columns: list[str]
    mapping: dict[str, str | None]
    fields: list[str]
    rows: list[dict[str, Any]]
    assets_to_create: list[dict[str, Any]]
    warnings: list[str]
    summary: dict[str, int]
    preview_token: str
    expires_at: str


class ImportApplyResult(BaseModel):
    clip_ids: list[int]
    scene_ids: list[int]
    created_assets: list[dict[str, Any]]
    unresolved_assets: list[str]
    dialogue_lines: int
    skipped: list[dict[str, Any]]
    change_set_id: int | None


@op("import.preview",
    summary="Xem trước nhập clip hàng loạt (dán dòng/CSV/XLSX/JSON/YAML/thư mục ảnh): tự ánh xạ cột, báo lỗi theo hàng, không ghi dữ liệu",
    params=ImportPreviewParams, result=ImportPreviewResult, idempotent=True, mcp="tool")
def import_preview(ctx: OpContext, p: ImportPreviewParams) -> dict:
    return importer.preview(ctx.services, sequence_id=p.sequence_id, source=p.source.model_dump(), mapping=p.mapping)


@op("import.apply",
    summary="Áp bản xem trước: tạo clip (+ cảnh, lời thoại, asset thiếu) trong một change set, hoàn tác được một lần",
    params=ImportApplyParams, result=ImportApplyResult, mcp="tool")
def import_apply(ctx: OpContext, p: ImportApplyParams) -> dict:
    return importer.apply(
        ctx, sequence_id=p.sequence_id, preview_token=p.preview_token,
        edits=[e.model_dump() for e in p.edits or []], create_assets=p.create_assets,
        after_clip_id=p.after_clip_id, skip_rows=p.skip_rows, skip_errors=p.skip_errors,
    )
