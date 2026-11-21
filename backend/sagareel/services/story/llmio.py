"""Gọi công việc LLM `story_*` và kiểm đầu ra.

- Prompt `story_*` là dữ liệu của project (bảng `project_prompt`, op prompt.set), biến có cấu trúc + `feedback`
  (góp ý khi viết lại sau `gate.reject`) + `fix_error` (lỗi định dạng của lần trước).
- Công việc `json_mode`: `data` được kiểm bằng model pydantic. JSON hỏng (`llm_invalid_json` từ `run_task`) hoặc sai
  schema → thử lại **một lần** với lỗi kiểm tra chèn vào `fix_error`; vẫn sai → `AppError("llm_bad_output")`.
- Công việc chữ (vd. viết kịch bản): rỗng/quá ngắn cũng được thử lại một lần.
- `llm_not_configured` (409) đi thẳng lên — người gọi chọn chế độ viết khác (manual/agent).
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from sagareel.core.errors import AppError
from sagareel.services import llm
from sagareel.services.jobs import JobFailed

M = TypeVar("M", bound=BaseModel)
MAX_ERROR_CHARS = 1500


# Kết quả do agent/người gửi thẳng (op `story.submit`): {task_key: dữ liệu}. Có khoá → không gọi LLM, kiểm bằng ĐÚNG
# schema của đường LLM rồi đi tiếp đường lưu như kết quả LLM (không ép agent qua LLM của tool).
GIVEN: ContextVar[dict[str, Any] | None] = ContextVar("story_given", default=None)
GIVEN_MODEL = "submitted"


@dataclass
class StoryOutcome:
    data: Any  # model pydantic đã kiểm (json) hoặc chuỗi (chữ)
    text: str
    model: str
    attempts: int
    usage: dict


def _errors_text(e: ValidationError) -> str:
    lines = []
    for err in e.errors()[:20]:
        loc = ".".join(str(x) for x in err.get("loc", ()))
        lines.append(f"- {loc or '(gốc)'}: {err.get('msg')}")
    return "\n".join(lines)[:MAX_ERROR_CHARS]


def bad_output(task_key: str, detail: str) -> AppError:
    return AppError("llm_bad_output", f"LLM trả kết quả sai định dạng cho '{task_key}' (đã thử lại một lần): {detail}",
                    hint="Thử model khác cho công việc này (llm.task_update), sửa prompt của dự án (prompt.set), hoặc tự viết/để agent viết.",
                    status=502, extra={"task": task_key})


async def run(services: Any, task_key: str, variables: dict[str, Any], schema: type[M] | None, *,
              min_chars: int = 1, on_delta: Callable[[str], None] | None = None,
              log: Callable[[str, str], None] | None = None, override: dict | None = None,
              project_id: int | None = None) -> StoryOutcome:
    """Chạy công việc `task_key`; `schema=None` = công việc trả chữ. Có kết quả gửi thẳng (`GIVEN`) → chỉ kiểm."""
    given = GIVEN.get()
    if given is not None and task_key in given:
        return _given(task_key, given[task_key], schema, min_chars)
    base = {"feedback": "", "fix_error": "", **variables}
    error = ""
    for attempt in (1, 2):
        vars_ = {**base, "fix_error": error}
        try:
            outcome = await llm.run_task(services, task_key, vars_, override=override, on_delta=on_delta, log=log,
                                         project_id=project_id)
        except JobFailed as e:
            if e.code != "llm_invalid_json":
                raise
            error = f"Câu trả lời không phải JSON hợp lệ: {e.msg}"[:MAX_ERROR_CHARS]
            if log is not None:
                log("warning", f"{task_key}: lần {attempt} trả JSON hỏng")
            continue
        if schema is None:
            body = (outcome.text or "").strip()
            if len(body) >= min_chars:
                return StoryOutcome(data=body, text=outcome.text, model=outcome.model, attempts=attempt,
                                    usage=outcome.usage)
            error = f"Câu trả lời quá ngắn ({len(body)} ký tự, cần ít nhất {min_chars})."
        else:
            try:
                data = schema.model_validate(outcome.data)
                return StoryOutcome(data=data, text=outcome.text, model=outcome.model, attempts=attempt,
                                    usage=outcome.usage)
            except ValidationError as e:
                error = "JSON sai cấu trúc:\n" + _errors_text(e)
        if log is not None:
            log("warning", f"{task_key}: lần {attempt} sai định dạng — {error.splitlines()[0]}")
    raise bad_output(task_key, error.splitlines()[0] if error else "không rõ")


def _given(task_key: str, value: Any, schema: type[M] | None, min_chars: int) -> StoryOutcome:
    from sagareel.core.errors import ValidationFailed, clean_validation_errors

    if schema is None:
        body = value.strip() if isinstance(value, str) else ""
        if len(body) < min_chars:
            raise ValidationFailed(f"Kết quả '{task_key}' phải là chữ, ít nhất {min_chars} ký tự.",
                                   code="submit_invalid")
        return StoryOutcome(data=body, text=body, model=GIVEN_MODEL, attempts=0, usage={})
    try:
        data = schema.model_validate(value)
    except ValidationError as e:
        raise ValidationFailed(f"Kết quả '{task_key}' sai cấu trúc ({schema.__name__}).", code="submit_invalid",
                               hint="Xem prompt.contracts (output_schema) để biết đúng định dạng.",
                               extra={"errors": clean_validation_errors(e.errors())}) from None
    return StoryOutcome(data=data, text="", model=GIVEN_MODEL, attempts=0, usage={})
