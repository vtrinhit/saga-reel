"""Prompt là **dữ liệu của project** (bảng `project_prompt`): không có bộ prompt chung của tool.

- `body(session, key, project)` → nội dung prompt `key` của project; thiếu → `AppError("prompt_missing")` kèm gợi ý
  (nhân bản từ dự án mẫu hoặc viết bằng `prompt.set` / để AI viết qua MCP). Không có nguồn dự phòng ẩn.
- Hợp đồng biến của từng khoá: `compile/prompt_contracts.py` (op `prompt.contracts`).
- Op: `prompt.list/get/set/delete_preview/delete/preview` (xem `ops/prompt_ops.py`).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from sagareel.compile import prompt_contracts as C
from sagareel.core import templating
from sagareel.core.context import OpContext
from sagareel.core.errors import AppError, NotFound, ValidationFailed
from sagareel.db.models import Project, ProjectPrompt
from sagareel.services import entities


def missing(key: str, project_id: int | None) -> AppError:
    where = f"dự án #{project_id}" if project_id is not None else "lần chạy không gắn dự án"
    return AppError(
        "prompt_missing", f"Thiếu prompt '{key}' cho {where}.",
        hint=f"Nhân bản từ một dự án mẫu, hoặc viết prompt '{key}' bằng prompt.set / để AI tạo qua MCP "
             "(xem prompt.contracts).",
        status=409, extra={"key": key, "project_id": project_id})


def _project_id(project: Any) -> int | None:
    if project is None:
        return None
    return project if isinstance(project, int) else project.id


def row(session: Session, project_id: int, key: str) -> ProjectPrompt | None:
    return session.scalars(select(ProjectPrompt).where(
        ProjectPrompt.project_id == project_id, ProjectPrompt.key == key, ProjectPrompt.deleted_at.is_(None)
    ).limit(1)).first()


def body(session: Session, key: str, project: Any) -> str:
    """Nội dung prompt `key` của project; thiếu → `prompt_missing` (409)."""
    pid = _project_id(project)
    r = row(session, pid, key) if pid is not None else None
    if r is None:
        raise missing(key, pid)
    return r.body


def bodies(session: Session, project_id: int) -> dict[str, str]:
    rows = session.scalars(select(ProjectPrompt).where(ProjectPrompt.project_id == project_id,
                                                       ProjectPrompt.deleted_at.is_(None))).all()
    return {r.key: r.body for r in rows}


def copy_all(session: Session, src_project_id: int, dst_project_id: int) -> int:
    """Chép mọi prompt của project nguồn sang project đích (khoá đã có ở đích bị ghi đè). Trả số khoá đã chép."""
    src = bodies(session, src_project_id)
    for key, text in src.items():
        put(session, dst_project_id, key, text)
    return len(src)


def put(session: Session, project_id: int, key: str, text: str) -> ProjectPrompt:
    r = row(session, project_id, key)
    if r is None:
        r = ProjectPrompt(project_id=project_id, key=key, body=text)
        session.add(r)
    elif r.body != text:
        r.body = text
    session.flush()
    return r


# --- Kiểm tra -------------------------------------------------------------------------------------------------------


def check_key(key: str) -> C.Contract:
    c = C.get(key)
    if c is None:
        raise ValidationFailed(f"Khoá prompt '{key}' không có trong hợp đồng của tool.", code="prompt_key_unknown",
                               hint="Gọi prompt.contracts để xem các khoá hợp lệ.",
                               extra={"keys": list(C.KEYS)})
    return c


def analyze(key: str, text: str) -> dict[str, Any]:
    """Kiểm cú pháp Jinja (sandbox, lỗi → `template_syntax_error`) + cảnh báo biến lạ/biến bắt buộc chưa dùng."""
    c = check_key(key)
    used = templating.variables_of(text)
    warnings = C.check_variables(key, used)
    unused = [n for n in c.variables if n not in used and n not in c.optional and n not in ("feedback", "fix_error")]
    if unused:
        warnings.append(f"Chưa dùng biến: {', '.join(unused)}.")
    return {"variables": used, "warnings": warnings}


# --- Op -------------------------------------------------------------------------------------------------------------


def item(r: ProjectPrompt, *, with_body: bool = True) -> dict[str, Any]:
    c = C.get(r.key)
    d = {"id": r.id, "project_id": r.project_id, "key": r.key, "label": c.label if c else r.key,
         "group": c.group if c else "check", "kind": c.kind if c else "llm", "chars": len(r.body),
         "updated_at": r.updated_at}
    if with_body:
        d["body"] = r.body
    return d


def list_prompts(ctx: OpContext, project_id: int) -> dict[str, Any]:
    with ctx.services.db.session() as s:
        entities.get(s, "project", project_id)
        rows = s.scalars(select(ProjectPrompt).where(ProjectPrompt.project_id == project_id,
                                                     ProjectPrompt.deleted_at.is_(None))).all()
        have = {r.key: r for r in rows}
        items = [item(have[k], with_body=False) for k in C.KEYS if k in have]
        items += [item(r, with_body=False) for k, r in sorted(have.items()) if k not in C.BY_KEY]
        return {"items": items, "missing": [k for k in C.KEYS if k not in have]}


def get_prompt(ctx: OpContext, project_id: int, key: str) -> dict[str, Any]:
    with ctx.services.db.session() as s:
        entities.get(s, "project", project_id)
        r = row(s, project_id, key)
        if r is None:
            raise NotFound(f"Dự án #{project_id} chưa có prompt '{key}'.", code="prompt_not_found",
                           hint="Viết bằng prompt.set (xem prompt.contracts để biết biến).")
        d = item(r)
    c = C.get(key)
    d["contract"] = C.to_dict(c) if c else None
    return d


def set_prompt(ctx: OpContext, project_id: int, key: str, text: str,
               expected_updated_at: Any = None) -> dict[str, Any]:
    info = analyze(key, text)
    with ctx.services.db.session() as s:
        entities.get(s, "project", project_id)
        existing = row(s, project_id, key)
        rid = existing.id if existing is not None else None
    if rid is not None:
        entities.patch(ctx, "project_prompt", rid, {"body": text}, expected_updated_at)
        with ctx.services.db.session() as s:
            out = item(entities.get(s, "project_prompt", rid))
    else:
        u = entities.uow(ctx, f"Viết prompt {key}", "prompt.set")
        u.project_id = project_id
        with u as s:
            out = item(put(s, project_id, key, text))
    return {**out, **info}


def delete_preview(ctx: OpContext, project_id: int, keys: list[str]) -> dict[str, Any]:
    with ctx.services.db.session() as s:
        ids = []
        for k in keys:
            r = row(s, project_id, k)
            if r is None:
                raise NotFound(f"Dự án #{project_id} chưa có prompt '{k}'.", code="prompt_not_found")
            ids.append(r.id)
    return entities.soft_delete_preview(ctx, "project_prompt", ids)


def delete(ctx: OpContext, confirm_token: str | None) -> dict[str, Any]:
    return entities.soft_delete(ctx, confirm_token, expect_type="project_prompt")


def _impact_extra(s: Session, rows: list[Any]) -> dict[str, Any]:
    return {"keys": [r.key for r in rows],
            "note": "Thiếu prompt thì bước dùng nó báo lỗi prompt_missing cho tới khi viết lại."}


entities.IMPACT_EXTRA["project_prompt"] = _impact_extra


def _patch_validator(s: Session, obj: ProjectPrompt, values: dict[str, Any]) -> dict[str, Any]:
    if "key" in values:
        raise ValidationFailed("Không đổi khoá prompt; xoá rồi viết khoá mới.", code="prompt_key_readonly")
    if "body" in values:
        if not isinstance(values["body"], str):
            raise ValidationFailed("body phải là chuỗi.")
        analyze(obj.key, values["body"])
    return values


entities.PATCH_VALIDATORS["project_prompt"] = _patch_validator


def project_of(s: Session, project_id: int) -> Project:
    return entities.get(s, "project", project_id)


# --- Kiểm tra tĩnh (không LLM, chỉ cảnh báo) -------------------------------------------------------------------------

# Bước quy trình → khoá prompt mà bước đó cần KHI DO LLM/compiler của tool làm (agent gửi thẳng kết quả thì không cần).
STEP_PROMPTS = {
    "write_bible": ("story_write_bible",), "outline": ("story_outline",), "write_script": ("story_write_script",),
    "split_clips": ("story_split_clips",), "image_prompts": ("story_image_prompts",),
    "motion_prompts": ("story_motion_prompts",), "dialogue": ("story_dialogue",),
    "memory_summarize": ("story_memory_summarize",), "generate_images": ("image.synced",),
    "generate_videos": ("motion.synced",),
}


def check_session(s: Session, project_id: int) -> dict[str, Any]:
    """Kiểm bộ prompt của dự án: cú pháp Jinja, khoá thiếu so với hợp đồng, biến lạ/chưa dùng, bước sẽ thiếu prompt
    khi chạy bằng LLM của tool. Chỉ cảnh báo — không bao giờ chặn chạy (bước thiếu prompt sẽ chờ nhập)."""
    have = bodies(s, project_id)
    warnings: list[dict[str, Any]] = []
    for key, text in sorted(have.items()):
        if C.get(key) is None:
            warnings.append({"key": key, "code": "prompt_key_unknown", "message": f"Khoá '{key}' không có trong hợp đồng."})
            continue
        try:
            info = analyze(key, text)
        except AppError as e:
            warnings.append({"key": key, "code": e.code, "message": e.detail})
            continue
        warnings += [{"key": key, "code": "prompt_variables", "message": w} for w in info["warnings"]]
    missing = [k for k in C.KEYS if k not in have]
    steps_without_prompt = {step: [k for k in ks if k not in have] for step, ks in STEP_PROMPTS.items()
                            if any(k not in have for k in ks)}
    for step, ks in steps_without_prompt.items():
        warnings.append({"key": ks[0], "code": "prompt_missing",
                         "message": f"Bước {step} chạy bằng LLM/compiler của tool sẽ thiếu prompt '{ks[0]}' → bước dừng "
                                    "chờ nhập (agent/người gửi kết quả bằng story.submit/prompts.submit, hoặc viết prompt)."})
    return {"project_id": project_id, "ok": not warnings, "missing": missing,
            "steps_without_prompt": steps_without_prompt, "warnings": warnings}


def check(ctx: OpContext, project_id: int) -> dict[str, Any]:
    with ctx.services.db.session() as s:
        entities.get(s, "project", project_id)
        return check_session(s, project_id)
