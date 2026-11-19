"""Tiện ích test của Task B (sequence/clip/take)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import event

from sagareel.core import rank
from sagareel.db.models import Clip, Take


def seed_clips(db, sequence_id: int, n: int, **fields: Any) -> list[int]:
    """Chèn nhanh `n` clip (rank tăng dần bằng `n_after`) — không qua UoW, chỉ để dựng dữ liệu test."""
    keys = rank.n_after(None, n)
    with db.session() as s:
        rows = [Clip(sequence_id=sequence_id, rank=k, key=f"k{i:05d}", **fields) for i, k in enumerate(keys)]
        s.add_all(rows)
        s.flush()
        return [r.id for r in rows]


def add_take(db, clip_id: int, part: str, *, media_id: int | None = None, status: str = "succeeded",
             cost: float = 0.0, **kw: Any) -> int:
    with db.session() as s:
        t = Take(owner_type="clip", owner_id=clip_id, part=part, status=status, media_id=media_id, cost=cost, **kw)
        s.add(t)
        s.flush()
        return t.id


class StatementCounter:
    """Đếm câu SQL khớp tiền tố (vd. "UPDATE clip") trên engine của `db`."""

    def __init__(self, db, prefix: str) -> None:
        self.engine = db.engine
        self.prefix = prefix.upper()
        self.statements: list[str] = []

    def _listen(self, conn, cursor, statement, parameters, context, executemany) -> None:
        if statement.strip().upper().startswith(self.prefix):
            n = len(parameters) if executemany else 1
            self.statements.extend([statement] * n)

    def __enter__(self) -> StatementCounter:
        event.listen(self.engine, "after_cursor_execute", self._listen)
        return self

    def __exit__(self, *exc) -> None:
        event.remove(self.engine, "after_cursor_execute", self._listen)

    @property
    def count(self) -> int:
        return len(self.statements)
