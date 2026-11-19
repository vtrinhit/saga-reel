"""Template prompt sửa được (spec §7.3): Jinja2 trong sandbox, biến thiếu là lỗi rõ ràng.

- `render(body, variables)` — `SandboxedEnvironment(undefined=StrictUndefined, autoescape=False,
  keep_trailing_newline=True)`. Giá trị biến được chèn **nguyên văn** (không strip, không escape, không đổi xuống
  dòng). Biến thiếu → `AppError("template_variable_missing", extra={"name"})`.
- `render_messages(body, variables)` — tách template thành tin nhắn theo vai bằng dòng đánh dấu
  `<!-- role: system -->` / `<!-- role: user -->` / `<!-- role: assistant -->` **trước** khi render, nên nội dung
  người dùng có chứa dòng đánh dấu cũng không đổi được vai. Không có dòng đánh dấu nào → một tin nhắn `user`.
  Phần chữ đứng trước dòng đánh dấu đầu tiên là ghi chú cho người sửa template, không gửi đi. Chỉ các ký tự xuống
  dòng ở hai đầu mã nguồn của mỗi phần bị bỏ (định dạng của template), nội dung biến không bao giờ bị cắt.
"""

from __future__ import annotations

import re
from typing import Any

from jinja2 import StrictUndefined, TemplateError, TemplateSyntaxError, UndefinedError, meta, nodes
from jinja2.exceptions import SecurityError
from jinja2.sandbox import SandboxedEnvironment

from sagareel.core.errors import AppError

__all__ = ["ROLES", "check_syntax", "render", "render_messages", "split_sections", "variables_of"]

ROLES = ("system", "user", "assistant")
_MARKER_RE = re.compile(r"^[ \t]*<!--[ \t]*role:[ \t]*(system|user|assistant)[ \t]*-->[ \t]*\r?$", re.MULTILINE)
_UNDEFINED_RE = re.compile(r"^'([^']+)' is undefined$")

_env = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False, keep_trailing_newline=True)


def _syntax_error(e: TemplateSyntaxError) -> AppError:
    return AppError(
        "template_syntax_error",
        f"Template sai cú pháp ở dòng {e.lineno}: {e.message}",
        hint="Sửa lại cú pháp Jinja ({{ biến }}, {% if %}…{% endif %}).",
        status=422,
        extra={"line": e.lineno},
    )


_LOADER_NODES = (nodes.Include, nodes.Import, nodes.FromImport, nodes.Extends)


def _parse(body: str):
    try:
        ast = _env.parse(body)
    except TemplateSyntaxError as e:
        raise _syntax_error(e) from None
    for node in ast.find_all(_LOADER_NODES):
        raise AppError(
            "template_syntax_error",
            f"Template dùng thẻ không hỗ trợ ({type(node).__name__.lower()}) ở dòng {node.lineno}.",
            hint="Template không được include/import/extends template khác; chép nội dung cần dùng vào thẳng.",
            status=422,
            extra={"line": node.lineno},
        )
    return ast


def variables_of(body: str) -> list[str]:
    """Tên biến cấp cao nhất template cần (sắp xếp). Sai cú pháp → `template_syntax_error`."""
    return sorted(meta.find_undeclared_variables(_parse(body)))


def check_syntax(body: str) -> None:
    _parse(body)


def _missing(name: str) -> AppError:
    return AppError(
        "template_variable_missing",
        f"Template cần biến '{name}' nhưng không được truyền.",
        hint=f"Truyền biến '{name}' trong variables, hoặc sửa template bỏ biến đó.",
        status=422,
        extra={"name": name},
    )


def render(body: str, variables: dict[str, Any]) -> str:
    """Render `body` với `variables`; giá trị biến chèn nguyên văn.

    Biến chỉ bị coi là thiếu khi template thật sự dùng tới giá trị của nó (`{{ x | default("…") }}` và
    `{% if x is defined %}` vẫn chạy khi không truyền `x`).
    """
    check_syntax(body)
    try:
        return _env.from_string(body).render(dict(variables))
    except TemplateSyntaxError as e:
        raise _syntax_error(e) from None
    except SecurityError as e:
        raise AppError(
            "template_unsafe",
            f"Template truy cập thuộc tính không được phép: {e}",
            hint="Template chỉ được dùng biến, vòng lặp, điều kiện và bộ lọc thông thường.",
            status=422,
        ) from None
    except UndefinedError as e:
        m = _UNDEFINED_RE.match(str(e.message or ""))
        if m:
            raise _missing(m.group(1)) from None
        raise AppError(
            "template_variable_missing",
            f"Template dùng giá trị không có: {e.message}",
            hint="Kiểm tra lại các biến (và thuộc tính của chúng) mà template dùng.",
            status=422,
            extra={"name": None},
        ) from None
    except TemplateError as e:
        raise _render_failed(e) from None
    except Exception as e:  # noqa: BLE001 — lỗi lúc render template người dùng (TypeError, ZeroDivision…) → 422
        raise _render_failed(e) from None


def _render_failed(e: Exception) -> AppError:
    return AppError(
        "template_render_failed",
        f"Không render được template: {type(e).__name__}: {str(e)[:300]}",
        hint="Sửa template (biểu thức, bộ lọc, kiểu dữ liệu của biến) rồi chạy lại.",
        status=422,
    )


def _trim_newlines(text: str) -> str:
    """Bỏ các ký tự xuống dòng ở hai đầu **mã nguồn template** (không đụng tới khoảng trắng khác, không đụng biến)."""
    return text.strip("\r\n")


def split_sections(body: str) -> list[tuple[str, str]]:
    """[(vai, mã nguồn template của phần đó)] theo dòng đánh dấu; không có đánh dấu → [("user", body)]."""
    marks = list(_MARKER_RE.finditer(body))
    if not marks:
        return [("user", body)]
    sections = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(body)
        sections.append((m.group(1), _trim_newlines(body[m.end():end])))
    return sections


def render_messages(body: str, variables: dict[str, Any]) -> list[dict[str, str]]:
    """Render từng phần theo vai → `[{"role", "content"}]` (bỏ phần rỗng sau khi render)."""
    check_syntax(body)
    out = []
    for role, source in split_sections(body):
        content = render(source, variables)
        if content != "":
            out.append({"role": role, "content": content})
    return out
