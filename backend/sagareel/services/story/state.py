"""Trạng thái chạy trong `sequence.pipeline` (JSON), chia theo vùng tên:

- `run`: trạng thái quy trình (`services.pipeline`), `memory`: bộ nhớ truyện của tập (tóm tắt, dàn ý — hook/điểm treo/
  từ khoá), `source`: đoạn truyện gốc khi chuyển thể, `signal`: cờ "có dữ liệu mới" (`nudge`) mà job quy trình đang
  chạy kiểm trước khi dừng chờ (chỉ job ghi `run`, người khác chỉ ghi `signal` → không đè nhau).

Ghi bằng `json_set` trên đúng một vùng trong phiên khoá ghi (không qua UnitOfWork): đây là trạng thái chạy, không phải
nội dung người dùng sửa — không được lọt vào lịch sử hoàn tác (Ctrl+Z không được "lùi" tiến độ quy trình). Đề xuất
migration: đánh dấu `sequence.pipeline`/`sequence.gates` là cột sổ sách (`info={"bookkeeping": True}`) hoặc tách cột
`sequence.memory`.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from sagareel.db.models import Sequence

NAMESPACES = ("run", "memory", "source", "signal")


def read(s: Session, sequence_id: int, ns: str) -> dict[str, Any]:
    raw = s.execute(text("SELECT pipeline FROM sequence WHERE id = :id"), {"id": sequence_id}).scalar()
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        data = {}
    value = data.get(ns) if isinstance(data, dict) else None
    return dict(value) if isinstance(value, dict) else {}


def of(seq: Sequence, ns: str) -> dict[str, Any]:
    value = (seq.pipeline or {}).get(ns)
    return dict(value) if isinstance(value, dict) else {}


def write(s: Session, sequence_id: int, ns: str, value: dict[str, Any]) -> None:
    """Thay cả vùng `ns` (trong phiên của người gọi)."""
    if ns not in NAMESPACES:
        raise ValueError(f"vùng trạng thái lạ: {ns}")
    s.execute(text(
        "UPDATE sequence SET pipeline = json_set(CASE WHEN json_valid(pipeline) THEN pipeline ELSE '{}' END, "
        ":path, json(:v)) WHERE id = :id"),
        {"path": f"$.{ns}", "v": json.dumps(value, ensure_ascii=False, default=str), "id": sequence_id})
    s.expire_all()


def merge(db: Any, sequence_id: int, ns: str, patch: dict[str, Any]) -> dict[str, Any]:
    """Gộp `patch` vào vùng `ns` (phiên khoá ghi riêng, đọc-sửa-ghi nguyên tử). Trả vùng mới."""
    with db.session(immediate=True) as s:
        cur = read(s, sequence_id, ns)
        cur.update(patch)
        write(s, sequence_id, ns, cur)
        return cur


def replace(db: Any, sequence_id: int, ns: str, value: dict[str, Any]) -> None:
    with db.session(immediate=True) as s:
        write(s, sequence_id, ns, value)
