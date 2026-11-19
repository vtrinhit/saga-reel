"""Lỗi chuẩn của ứng dụng: mọi cửa (REST, CLI, MCP) trả cùng một hình `payload()`.

`error_code` là tiếng Anh snake_case ổn định; `detail`/`hint` là tiếng Việt có dấu cho người dùng.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Literal

from pydantic import BaseModel


class AppError(Exception):
    """Lỗi có chủ đích, biến thành JSON `payload()` với HTTP status tương ứng."""

    def __init__(
        self,
        code: str,
        detail: str,
        *,
        hint: str | None = None,
        status: int = 400,
        extra: dict | None = None,
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.hint = hint
        self.status = status
        self.extra = extra if extra is not None else {}

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r}, status={self.status}, detail={self.detail!r})"


class _PresetError(AppError):
    """Lớp con có sẵn `code` và `status`; `code` vẫn ghi đè được khi cần mã cụ thể hơn."""

    default_code: str = "error"
    default_status: int = 400

    def __init__(
        self,
        detail: str,
        *,
        hint: str | None = None,
        extra: dict | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(code or self.default_code, detail, hint=hint, status=self.default_status, extra=extra)


class NotFound(_PresetError):
    default_code, default_status = "not_found", 404


class Forbidden(_PresetError):
    default_code, default_status = "forbidden", 403


class ConfirmRequired(_PresetError):
    default_code, default_status = "confirm_required", 409


class Conflict(_PresetError):
    default_code, default_status = "conflict", 409


class ValidationFailed(_PresetError):
    default_code, default_status = "validation_failed", 422


class ErrorPayload(BaseModel):
    """Hình JSON của mọi lỗi (dùng để mô tả trong OpenAPI; dữ liệu thật dựng bằng `payload()`)."""

    ok: Literal[False] = False
    error_code: str
    detail: str
    hint: str | None = None
    status: int
    extra: dict[str, Any] = {}


def payload(e: AppError) -> dict:
    return {
        "ok": False,
        "error_code": e.code,
        "detail": e.detail,
        "hint": e.hint,
        "status": e.status,
        "extra": e.extra,
    }


def internal_error() -> AppError:
    """Lỗi không lường trước: không lộ chi tiết ra response (traceback chỉ nằm trong log)."""
    return AppError(
        "internal_error",
        "Đã xảy ra lỗi nội bộ không lường trước.",
        hint="Xem log của máy chủ để biết chi tiết.",
        status=500,
    )


def clean_validation_errors(errors: Iterable[Mapping[str, Any]], *, strip_body: bool = False) -> list[dict]:
    """Rút gọn lỗi Pydantic/FastAPI thành `{loc, msg, type}`.

    Bỏ `input` và `ctx`: `input` có thể chứa bí mật (API key), `ctx` có thể chứa object không serialize được.
    `strip_body=True` bỏ tiền tố `"body"` của FastAPI để `loc` giống hệt lỗi do `ops.invoke` tự validate.
    """
    out = []
    for err in errors:
        loc = list(err.get("loc", ()))
        if strip_body and loc[:1] == ["body"]:
            loc = loc[1:]
        out.append({"loc": loc, "msg": str(err.get("msg", "")), "type": str(err.get("type", ""))})
    return out


def validation_failed(errors: Iterable[Mapping[str, Any]], *, strip_body: bool = False) -> ValidationFailed:
    return ValidationFailed(
        "Dữ liệu đầu vào không hợp lệ.",
        hint="Xem `extra.errors` để biết trường nào sai.",
        extra={"errors": clean_validation_errors(errors, strip_body=strip_body)},
    )
