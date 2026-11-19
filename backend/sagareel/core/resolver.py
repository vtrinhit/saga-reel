"""Resolver cài đặt theo tầng (spec §5.4).

Schema là dữ liệu (`defaults/settings_schema.yaml`). Mỗi tầng chỉ lưu các khoá nó ghi đè; giá trị của một khoá lấy
từ tầng cao nhất có khoá đó theo thứ tự `LAYERS` (tầng sau thắng), không có thì lấy mặc định của schema. Kết quả luôn
kèm tầng nguồn (`source`, `source_id`) để UI hiện SourceBadge.

```python
r = Resolver(load_schema(SCHEMA_PATH))
r.resolve("video.duration", [Layer("global", 1, {"video.duration": 10})])  # → value 10, source "global"
```

Kiểm tra kiểu/`choices` chỉ áp khi **ghi** (`validate_values`); đọc thì dung nạp: khoá lạ trong dữ liệu tầng bị bỏ
qua, giá trị đã lưu được trả nguyên.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict

from sagareel.core.errors import AppError, ValidationFailed

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "defaults" / "settings_schema.yaml"

LAYERS = ("default", "global", "preset", "project", "sequence", "scene", "clip", "run")
_RANK = {name: i for i, name in enumerate(LAYERS)}

SettingType = Literal["str", "int", "float", "bool", "enum", "json"]
_TYPES = ("str", "int", "float", "bool", "enum", "json")
_FIELDS = {"type", "default", "choices", "description", "secret"}
INTERNAL_PREFIX = "_"  # khoá nội bộ của global_settings.data, không thuộc schema


class SettingDef(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    type: SettingType
    default: Any
    choices: list | None = None
    description: str
    secret: bool = False


class Resolved(BaseModel):
    key: str
    value: Any
    source: str  # một trong LAYERS
    source_id: int | None = None


@dataclass(frozen=True)
class Layer:
    """Một tầng ghi đè: `name` thuộc `LAYERS`, `id` của thực thể mang tầng (vd. id project), `data` khoá → giá trị."""

    name: str
    id: int | None
    data: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.name not in _RANK or self.name == "default":
            raise ValueError(f"Tầng cài đặt không hợp lệ: {self.name!r} (cần một trong {LAYERS[1:]})")


# --- Kiểm tra giá trị -------------------------------------------------------------------------------------------


def _same(a: Any, b: Any) -> bool:
    """So sánh chặt kiểu: `True` ≠ `1`, `"6"` ≠ `6`."""
    return type(a) is type(b) and a == b


def _type_error(d: SettingDef, value: Any) -> str | None:
    """Thông báo lỗi tiếng Việt nếu `value` không hợp với `d`, ngược lại `None`."""
    t = d.type
    if t == "str":
        ok = isinstance(value, str)
    elif t == "int":
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif t == "float":
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif t == "bool":
        ok = isinstance(value, bool)
    elif t == "enum":
        ok = True  # kiểm qua choices bên dưới
    else:  # json
        try:
            json.dumps(value, allow_nan=False)
            ok = True
        except (TypeError, ValueError):
            ok = False
    if not ok:
        return f"Giá trị của '{d.key}' phải có kiểu {t}."
    if d.choices is not None and not any(_same(value, c) for c in d.choices):
        allowed = ", ".join(json.dumps(c, ensure_ascii=False) for c in d.choices)
        return f"Giá trị của '{d.key}' phải là một trong: {allowed}."
    return None


# --- Nạp schema -------------------------------------------------------------------------------------------------


def _parse_def(key: Any, raw: Any) -> SettingDef:
    if not isinstance(key, str) or not key or key.startswith(INTERNAL_PREFIX):
        raise ValueError(f"Tên khoá cài đặt không hợp lệ: {key!r} (không được rỗng hay bắt đầu bằng '_').")
    if not isinstance(raw, dict):
        raise ValueError(f"Định nghĩa của '{key}' phải là một mapping.")
    extra = set(raw) - _FIELDS
    if extra:
        raise ValueError(f"Định nghĩa của '{key}' có trường lạ: {sorted(extra)}.")
    if raw.get("type") not in _TYPES:
        raise ValueError(f"Kiểu của '{key}' phải là một trong {_TYPES}.")
    if "default" not in raw or not isinstance(raw.get("description"), str) or not raw["description"].strip():
        raise ValueError(f"'{key}' cần có default và description.")
    choices = raw.get("choices")
    if raw["type"] == "enum" and not choices:
        raise ValueError(f"'{key}' kiểu enum cần danh sách choices.")
    if choices is not None and not isinstance(choices, list):
        raise ValueError(f"choices của '{key}' phải là danh sách.")
    if not isinstance(raw.get("secret", False), bool):
        raise ValueError(f"secret của '{key}' phải là true/false.")
    d = SettingDef(key=key, **raw)
    err = _type_error(d, d.default)
    if err:
        raise ValueError(f"Mặc định không hợp lệ: {err}")
    return d


def load_schema(path: Path) -> dict[str, SettingDef]:
    """Đọc schema YAML → `{key: SettingDef}` theo thứ tự trong file. Schema sai → `ValueError` (lỗi lập trình)."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"Schema cài đặt {path} phải là một mapping khoá → định nghĩa.")
    return {key: _parse_def(key, value) for key, value in raw.items()}


# --- Resolver ---------------------------------------------------------------------------------------------------


def unknown_setting(key: str, detail: str | None = None) -> AppError:
    return AppError(
        "unknown_setting",
        detail or f"Không có cài đặt nào tên '{key}'.",
        hint="Gọi settings.schema để xem danh sách khoá cài đặt.",
        extra={"key": key},
    )


class Resolver:
    def __init__(self, schema: dict[str, SettingDef]) -> None:
        self.schema = schema

    def definition(self, key: str) -> SettingDef:
        d = self.schema.get(key)
        if d is None:
            raise unknown_setting(key)
        return d

    def resolve(self, key: str, layers: list[Layer]) -> Resolved:
        """Giá trị hiệu lực của `key`: tầng cao nhất (theo `LAYERS`) có khoá thắng; thứ tự trong `layers` không quan
        trọng (hai tầng cùng tên thì cái đứng sau thắng)."""
        d = self.definition(key)
        best: Layer | None = None
        for layer in layers:
            if key in layer.data and (best is None or _RANK[layer.name] >= _RANK[best.name]):
                best = layer
        if best is None:
            return Resolved(key=key, value=copy.deepcopy(d.default), source="default", source_id=None)
        return Resolved(key=key, value=copy.deepcopy(best.data[key]), source=best.name, source_id=best.id)

    def explain(self, layers: list[Layer]) -> list[Resolved]:
        """Mọi khoá trong schema (theo thứ tự schema) kèm giá trị hiệu lực và tầng nguồn."""
        return [self.resolve(key, layers) for key in self.schema]

    def validate_values(self, values: dict[str, Any]) -> dict[str, Any]:
        """Kiểm tra giá trị trước khi ghi vào một tầng. Khoá lạ/nội bộ → `unknown_setting`; sai kiểu hoặc ngoài
        `choices` → `ValidationFailed` (`extra.errors` liệt kê từng khoá). Trả lại `values`."""
        for key in values:
            self.definition(key)
        errors = []
        for key, value in values.items():
            msg = _type_error(self.schema[key], value)
            if msg:
                errors.append({"loc": [key], "msg": msg, "type": "setting_invalid"})
        if errors:
            raise ValidationFailed(
                "Giá trị cài đặt không hợp lệ.",
                hint="Xem `extra.errors`; gọi settings.schema để biết kiểu và lựa chọn của từng khoá.",
                extra={"errors": errors},
            )
        return values


def default_resolver() -> Resolver:
    """Resolver từ schema đóng gói sẵn."""
    return Resolver(load_schema(SCHEMA_PATH))
