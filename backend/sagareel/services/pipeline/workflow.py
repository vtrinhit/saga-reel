"""Quy trình (workflow) — **dữ liệu của project** (`project.workflows = {key: {...}}`): danh sách bước có thứ tự.

Quy trình mặc định của tool nằm ở `defaults/workflows/*.yaml` và được chép vào project khi tạo/nhân bản (`defaults()`);
sửa quy trình = sửa `project.workflows` (project.update), không ảnh hưởng project khác.

```yaml
data:
  label: …
  batch: {mode: parallel|sequential, release_after: outline}
  steps:
    - {step: write_bible}
    - {step: gate:outline, enabled: false, rerun: [outline]}
    - {step: render_preview, params: {seconds: 15}}
```

- `step`: một trong `STEPS` hoặc `gate:<tên>` (gate dừng ở `waiting_review`). `enabled` mặc định true.
- `rerun` (chỉ gate): các bước chạy lại (nhận `feedback`) khi `gate.reject`; mặc định = bước bật gần nhất phía trước.
- `batch.release_after`: chạy nhiều tập — tập đầu ("tập dẫn") chạy trước, các tập sau được thả khi tập dẫn xong bước
  này (`parallel`: thả hết; `sequential`: thả lần lượt từng tập, mỗi tập thả tập kế khi xong bước này). Bật
  `gate:pilot` → tập dẫn chỉ thả lô sau khi gate pilot được duyệt ("thử một tập đầu").
"""

from __future__ import annotations

import functools
import re
from pathlib import Path
from typing import Any, Literal

import yaml

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy.orm import Session

from sagareel.core.errors import NotFound, ValidationFailed, clean_validation_errors

WORKFLOWS_DIR = Path(__file__).resolve().parent.parent.parent / "defaults" / "workflows"

LLM_STEPS = ("write_bible", "outline", "write_script", "split_clips", "image_prompts", "motion_prompts", "dialogue",
             "memory_summarize")
JOB_STEPS = ("generate_images", "generate_videos", "generate_audio", "subtitles", "render_animatic",
             "render_preview", "render", "export")
OTHER_STEPS = ("auto_select",)
STEPS = LLM_STEPS + JOB_STEPS + OTHER_STEPS
GATE_RE = re.compile(r"^gate:[a-z][a-z0-9_]*$")
LABELS = {
    "write_bible": "Viết Story Bible", "outline": "Lập dàn ý", "write_script": "Viết kịch bản",
    "split_clips": "Chia clip", "image_prompts": "Prompt ảnh", "motion_prompts": "Prompt chuyển động",
    "dialogue": "Lời thoại", "memory_summarize": "Ghi bộ nhớ truyện", "generate_images": "Tạo ảnh",
    "generate_videos": "Tạo video", "generate_audio": "Tạo âm thanh", "subtitles": "Phụ đề",
    "render_animatic": "Dựng animatic", "render_preview": "Dựng xem trước", "render": "Dựng bản cuối",
    "export": "Xuất", "auto_select": "Tự chọn take",
}
DEFAULT_WORKFLOW = "full_auto"


def is_gate(name: str) -> bool:
    return name.startswith("gate:")


def label(name: str) -> str:
    if is_gate(name):
        return f"Duyệt {name[5:]}"
    return LABELS.get(name, name)


class StepDef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step: str
    enabled: bool = True
    rerun: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _known(self) -> StepDef:
        if self.step not in STEPS and not GATE_RE.match(self.step):
            raise ValueError(f"bước lạ '{self.step}' (bước hợp lệ: {', '.join(STEPS)} hoặc gate:<tên>)")
        if self.rerun and not is_gate(self.step):
            raise ValueError("chỉ gate mới có rerun")
        return self


class BatchDef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["parallel", "sequential"] = "parallel"
    release_after: str = "outline"


class WorkflowData(BaseModel):
    model_config = ConfigDict(extra="allow")

    label: str = ""
    description: str = ""
    batch: BatchDef = Field(default_factory=BatchDef)
    steps: list[StepDef] = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> WorkflowData:
        names = [s.step for s in self.steps]
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            raise ValueError(f"bước lặp lại: {', '.join(dup)}")
        for i, s in enumerate(self.steps):
            bad = [r for r in s.rerun if r not in names[:i] or is_gate(r)]
            if bad:
                raise ValueError(f"{s.step}: rerun phải là bước (không phải gate) đứng trước: {', '.join(bad)}")
        if self.batch.release_after not in names:
            raise ValueError(f"batch.release_after '{self.batch.release_after}' không có trong steps")
        return self


def validate(data: Any) -> dict[str, Any]:
    try:
        return WorkflowData.model_validate(data).model_dump()
    except ValidationError as e:
        raise ValidationFailed("Quy trình không hợp lệ.", code="workflow_invalid",
                               hint="Xem `extra.errors`.", extra={"errors": clean_validation_errors(e.errors())}
                               ) from None


def validate_all(workflows: Any) -> dict[str, Any]:
    """`project.workflows`: {key: dữ liệu quy trình}; mỗi quy trình kiểm như `validate` (giữ `name`)."""
    if not isinstance(workflows, dict):
        raise ValidationFailed("workflows phải là object {khoá: quy trình}.", code="workflow_invalid")
    out: dict[str, Any] = {}
    for key, data in workflows.items():
        if not isinstance(key, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", key):
            raise ValidationFailed(f"Khoá quy trình '{key}' không hợp lệ.", code="workflow_invalid")
        validate(data)
        out[key] = dict(data)
    return out


@functools.lru_cache(maxsize=1)
def _defaults() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(WORKFLOWS_DIR.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        key = str(raw.get("key") or path.stem)
        data = dict(raw.get("data") or {})
        validate(data)
        out[key] = {"name": str(raw.get("name") or key), **data}
    return out


def defaults() -> dict[str, dict[str, Any]]:
    """Quy trình mặc định của tool (bản sao mới) — chép vào project mới."""
    import copy

    return copy.deepcopy(_defaults())


def load(s: Session, key: str, project_id: int | None) -> dict[str, Any]:
    """Quy trình `key` của project; chưa có project (ước tính trước khi tạo) → quy trình mặc định của tool."""
    from sagareel.db.models import Project

    if project_id is not None:
        p = s.get(Project, project_id)
        pool = dict(p.workflows or {}) if p is not None else {}
    else:
        pool = _defaults()
    if key not in pool:
        raise NotFound(f"Không có quy trình '{key}'.", code="workflow_not_found",
                       hint=f"Quy trình có: {', '.join(sorted(pool)) or '(chưa có)'} — xem project.workflows; "
                            "thêm/sửa bằng project.update {workflows: …}.")
    return validate(pool[key])


def apply_overrides(wf: dict[str, Any], overrides: dict[str, bool]) -> list[dict[str, Any]]:
    """Danh sách bước với `enabled` đã ghi đè (`{"gate:pilot": true, "export": false}`); bước lạ → lỗi."""
    names = {s["step"] for s in wf["steps"]}
    unknown = sorted(k for k in overrides if k not in names)
    if unknown:
        raise ValidationFailed(f"Bước không có trong quy trình: {', '.join(unknown)}.", code="workflow_step_unknown")
    return [{**s, "enabled": bool(overrides.get(s["step"], s["enabled"]))} for s in wf["steps"]]
