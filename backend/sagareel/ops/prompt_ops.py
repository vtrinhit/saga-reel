"""Thao tác nhóm `prompt`: prompt là dữ liệu của project; tool công bố hợp đồng (biến, đầu ra, op dùng tới).

Luồng cho AI qua MCP: `prompt.contracts` → viết từng khoá bằng `prompt.set` → `prompt.preview` với dữ liệu thật →
(tuỳ chọn) đánh dấu dự án là mẫu (`project.update {is_sample: true}`) để nhân bản cho dự án sau.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import anyio
from pydantic import BaseModel, Field
from sqlalchemy import select

from sagareel.compile import compiler
from sagareel.compile import prompt_contracts as C
from sagareel.core import templating
from sagareel.core.context import OpContext
from sagareel.core.errors import AppError, NotFound
from sagareel.core.ops import op
from sagareel.db.models import Asset, AssetVariant, Clip, Sequence
from sagareel.services import confirm, entities, prompts


class ContractsParams(BaseModel):
    key: str | None = Field(default=None, description="Chỉ một khoá; bỏ trống = mọi khoá")


@op("prompt.contracts", summary="Hợp đồng prompt của tool: mỗi khoá nhận biến gì, trả gì (JSON schema), op nào dùng",
    params=ContractsParams, read_only=True, idempotent=True, viewer=True, mcp="tool")
def prompt_contracts(ctx: OpContext, p: ContractsParams) -> dict:
    items = [C.to_dict(c) for c in C.CONTRACTS if p.key is None or c.key == p.key]
    if p.key is not None and not items:
        prompts.check_key(p.key)
    return {"items": items, "groups": C.GROUPS,
            "syntax": "Jinja2 sandbox (không include/import); LLM: dòng `<!-- role: system|user|assistant -->` tách "
                      "vai; giá trị biến chèn nguyên văn."}


class ProjectParams(BaseModel):
    project_id: int


@op("prompt.list", summary="Prompt của dự án (không kèm nội dung) + các khoá còn thiếu so với hợp đồng",
    params=ProjectParams, read_only=True, idempotent=True, viewer=True, mcp="tool")
def prompt_list(ctx: OpContext, p: ProjectParams) -> dict:
    return prompts.list_prompts(ctx, p.project_id)


@op("prompt.check", summary="Kiểm bộ prompt của dự án (không LLM, chỉ cảnh báo): cú pháp, khoá thiếu, biến lạ, bước "
    "sẽ thiếu prompt khi chạy bằng LLM của tool", params=ProjectParams, read_only=True, idempotent=True, viewer=True,
    mcp="tool")
def prompt_check(ctx: OpContext, p: ProjectParams) -> dict:
    return prompts.check(ctx, p.project_id)


class KeyParams(BaseModel):
    project_id: int
    key: str


@op("prompt.get", summary="Nội dung một prompt của dự án kèm hợp đồng biến của khoá đó",
    params=KeyParams, read_only=True, idempotent=True, viewer=True, mcp="tool")
def prompt_get(ctx: OpContext, p: KeyParams) -> dict:
    return prompts.get_prompt(ctx, p.project_id, p.key)


class SetParams(BaseModel):
    project_id: int
    key: str = Field(description="Khoá trong prompt.contracts, vd. image.synced, story_write_script")
    body: str = Field(description="Thân Jinja (lưu nguyên văn)")
    expected_updated_at: datetime | None = Field(default=None, description="`updated_at` lúc đọc (chống ghi đè)")


@op("prompt.set", summary="Viết/sửa prompt của dự án (kiểm cú pháp Jinja sandbox; cảnh báo biến lạ so với hợp đồng)",
    params=SetParams, idempotent=True, mcp="tool")
def prompt_set(ctx: OpContext, p: SetParams) -> dict:
    return prompts.set_prompt(ctx, p.project_id, p.key, p.body, p.expected_updated_at)


class DeletePreviewParams(BaseModel):
    project_id: int
    keys: list[str] = Field(min_length=1, max_length=50)


class DeletePreviewOut(BaseModel):
    impact: dict[str, Any]
    confirm_token: str
    expires_in_s: int = confirm.TTL_S


@op("prompt.delete_preview", summary="Xem tác động của việc xoá prompt và lấy confirm_token", params=DeletePreviewParams,
    result=DeletePreviewOut, read_only=True, idempotent=True, viewer=False, mcp="tool")
def prompt_delete_preview(ctx: OpContext, p: DeletePreviewParams) -> dict:
    return prompts.delete_preview(ctx, p.project_id, p.keys)


class DeleteParams(BaseModel):
    confirm_token: str | None = Field(default=None, description="Lấy từ prompt.delete_preview")


@op("prompt.delete", summary="Xoá (mềm, hoàn tác được) prompt của dự án — cần confirm_token", params=DeleteParams,
    destructive=True, mcp="tool")
def prompt_delete(ctx: OpContext, p: DeleteParams) -> dict:
    return prompts.delete(ctx, p.confirm_token)


class PreviewParams(BaseModel):
    project_id: int
    key: str
    body: str | None = Field(default=None, description="Thân cần thử (chưa lưu); bỏ trống = prompt đang lưu")
    clip_id: int | None = Field(default=None, description="Clip mẫu (image.synced/motion.synced); bỏ trống = clip "
                                "đầu tiên của dự án")
    look_id: int | None = Field(default=None, description="Look mẫu (sheet.*); bỏ trống = look đầu tiên đúng loại")
    variables: dict[str, Any] | None = Field(default=None, description="Biến thử cho prompt LLM; thiếu biến nào thì "
                                             "dùng chữ giữ chỗ <tên biến>")


def _first_clip(s: Any, project_id: int) -> int | None:
    return s.scalar(select(Clip.id).join(Sequence, Sequence.id == Clip.sequence_id).where(
        Sequence.project_id == project_id, Sequence.deleted_at.is_(None), Clip.deleted_at.is_(None))
        .order_by(Sequence.number, Clip.rank, Clip.id).limit(1))


def _first_look(s: Any, project_id: int, kind: str) -> int | None:
    return s.scalar(select(AssetVariant.id).join(Asset, Asset.id == AssetVariant.asset_id).where(
        Asset.project_id == project_id, Asset.kind == kind, Asset.deleted_at.is_(None),
        AssetVariant.deleted_at.is_(None)).order_by(Asset.id, AssetVariant.id).limit(1))


@op("prompt.preview", summary="Xem trước prompt với dữ liệu thật (clip/look của dự án) hoặc biến thử (LLM)",
    params=PreviewParams, read_only=True, idempotent=True, mcp="tool")
async def prompt_preview(ctx: OpContext, p: PreviewParams) -> dict:
    c = prompts.check_key(p.key)

    def body() -> str:
        if p.body is not None:
            prompts.analyze(p.key, p.body)
            return p.body
        with ctx.services.db.session() as s:
            return prompts.body(s, p.key, p.project_id)

    text = await anyio.to_thread.run_sync(body)
    info = prompts.analyze(p.key, text)
    if c.kind == "llm":
        names = templating.variables_of(text)
        variables = {n: f"<{n}>" for n in names}
        variables.update(p.variables or {})
        messages = templating.render_messages(text, variables)
        return {"key": p.key, "kind": "llm", "messages": messages, **info}

    if p.key in (compiler.IMAGE_TEMPLATE, compiler.MOTION_TEMPLATE):
        def pick() -> int:
            with ctx.services.db.session() as s:
                entities.get(s, "project", p.project_id)
                cid = p.clip_id if p.clip_id is not None else _first_clip(s, p.project_id)
                if cid is None:
                    raise AppError("preview_no_sample", "Dự án chưa có clip để xem trước.", status=409,
                                   hint="Tạo một tập + clip (hoặc truyền clip_id của dự án khác cùng prompt).")
                return cid

        cid = await anyio.to_thread.run_sync(pick)
        if p.key == compiler.IMAGE_TEMPLATE:
            await compiler.prepare_clip(ctx.services, cid)

        def run() -> dict:
            with ctx.services.db.session() as s:
                fn = compiler.compile_clip_image if p.key == compiler.IMAGE_TEMPLATE else compiler.compile_clip_motion
                return compiler.to_dict(fn(s, cid, override=text))

        res = await anyio.to_thread.run_sync(run)
        return {"key": p.key, "kind": "compile", "clip_id": cid, **res, "variables": info["variables"],
                "warnings": [*res["warnings"], *info["warnings"]]}

    kind = p.key.split(".", 1)[1]

    def run_sheet() -> dict:
        with ctx.services.db.session() as s:
            entities.get(s, "project", p.project_id)
            lid = p.look_id if p.look_id is not None else _first_look(s, p.project_id, kind)
            if lid is None:
                raise NotFound(f"Dự án chưa có look loại {kind} để xem trước.", code="preview_no_sample")
            cp = compiler.compile_sheet(s, lid, compiler.ANCHOR, override=text)
            return {"look_id": lid, **compiler.to_dict(cp)}

    res = await anyio.to_thread.run_sync(run_sheet)
    return {"key": p.key, "kind": "compile", **res, "variables": info["variables"],
            "warnings": [*res["warnings"], *info["warnings"]]}


class GuideParams(BaseModel):
    project_id: int | None = Field(default=None, description="Kèm trạng thái của dự án: tập, clip, prompt thiếu, chờ gì")


@op("studio.guide", summary="Bản đồ quy trình làm phim cho agent: từng bước (mục đích, vào/ra + schema, op tool làm, op "
    "agent tự gửi, prompt liên quan, gate) + trạng thái dự án", params=GuideParams, read_only=True, idempotent=True,
    viewer=True, mcp="tool")
def studio_guide(ctx: OpContext, p: GuideParams) -> dict:
    from sagareel.services import guide

    return guide.guide(ctx, p.project_id)
