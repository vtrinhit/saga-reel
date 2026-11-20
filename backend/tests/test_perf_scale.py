"""M8 — hiệu năng ở quy mô lớn: 200 tập, 1000 clip trong một tập, 50 job (spec §15, §19, §20).

Đánh dấu `slow`: mặc định bị loại khỏi lần chạy thường (xem `addopts` trong pyproject); chạy bằng
`pytest backend/tests/test_perf_scale.py -m slow`. Ngân sách ≈ 3× số đo trên máy dev; ngoài thời gian còn khoá số câu
SQL (không N+1) và kiểm không có quét toàn bảng trên bảng lớn (EXPLAIN QUERY PLAN).
Đặt `SAGAREEL_PERF_JSON=<file>` để ghi bảng số đo (dùng cho docs/perf/m8-scale.md).
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import pytest
from sqlalchemy import event

pytestmark = pytest.mark.slow

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

# op → (ngân sách ms, ngân sách số câu SQL). ms ≈ 3× số đo trên máy dev (Windows, SQLite WAL); số câu SQL là trần cứng
# chống N+1 (không phụ thuộc máy).
BUDGETS: dict[str, tuple[float, int]] = {
    "clip.list first (200)": (100, 5),
    "clip.list cursor pages (1000)": (700, 20),
    "clip.move (1 of 1000)": (40, 14),
    "sequence.list (200 eps)": (70, 8),
    "project.get": (40, 10),
    "review.queue": (200, 8),
    "sequence.lint": (500, 5),
    "sequence.cost_summary": (70, 7),
    "pipeline.status global": (40, 3),
    "history.list": (40, 4),
    "job.list (50)": (40, 4),
    "bible.search": (40, 6),
    "layout (load + compute, 1000 clips)": (1300, 25),
    "import.preview (1000 rows)": (120, 8),
    "project.export_spec (200 eps, 5k clips)": (8000, 1000),
}

# Bảng có thể lớn tuỳ ý (không bị chặn bởi quy mô spec); job/sequence/bible_item nhỏ và quét theo rowid + LIMIT thì rẻ.
BIG_TABLES = {"clip", "dialogue_line", "take", "revision", "change_set"}


class Probe:
    def __init__(self, db) -> None:
        self.engine = db.engine
        self.stmts: list[tuple[str, object]] = []

    def _on(self, conn, cursor, statement, parameters, context, executemany) -> None:
        if not executemany:
            self.stmts.append((statement, parameters))
        else:
            self.stmts.append((statement, None))

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._on)
        return self

    def __exit__(self, *exc) -> None:
        event.remove(self.engine, "before_cursor_execute", self._on)

    @property
    def count(self) -> int:
        return len(self.stmts)

    def scans(self) -> list[str]:
        """Quét toàn bảng (SCAN <bảng lớn> không dùng chỉ mục) trong các câu SELECT đã chạy."""
        bad: list[str] = []
        with self.engine.connect() as c:
            for stmt, params in self.stmts:
                if not stmt.lstrip().upper().startswith("SELECT") or params is None:
                    continue
                try:
                    plan = c.exec_driver_sql("EXPLAIN QUERY PLAN " + stmt, params).fetchall()
                except Exception as e:  # noqa: BLE001 — câu không giải thích được (PRAGMA…) thì bỏ qua
                    print(f"[perf] bỏ qua EXPLAIN: {e}")
                    continue
                for row in plan:
                    detail = str(row[-1])
                    m = re.match(r"SCAN (\w+)\s*$", detail) or re.match(r"SCAN (\w+) (?!USING)", detail)
                    if m and m.group(1) in BIG_TABLES and "USING" not in detail:
                        bad.append(f"{detail} <- {' '.join(stmt.split())[:140]}")
        return bad


@pytest.fixture(scope="module")
def big(tmp_path_factory):
    import seed_scale

    data_dir = tmp_path_factory.mktemp("scale")
    info = seed_scale.seed(data_dir, log=lambda *_: None)
    yield info
    info["call"].close()
    info["db"].dispose()


RESULTS: dict[str, dict] = {}


def measure(name: str, big, fn, *, repeat: int = 3):
    """Chạy `fn` `repeat` lần: lấy lần nhanh nhất (ms) + số câu SQL của lần cuối; kiểm ngân sách."""
    best = float("inf")
    probe = None
    out = None
    for _ in range(repeat):
        with Probe(big["db"]) as p:
            t0 = time.perf_counter()
            out = fn()
            dt = (time.perf_counter() - t0) * 1000
        best, probe = min(best, dt), p
    budget_ms, budget_q = BUDGETS[name]
    scans = probe.scans()
    RESULTS[name] = {"ms": round(best, 1), "queries": probe.count, "scans": scans}
    path = os.environ.get("SAGAREEL_PERF_JSON")
    if path:
        Path(path).write_text(json.dumps(RESULTS, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n[perf] {name}: {best:.1f} ms, {probe.count} SQL, scans={scans}")
    assert best <= budget_ms, f"{name}: {best:.0f} ms > {budget_ms} ms"
    assert probe.count <= budget_q, f"{name}: {probe.count} câu SQL > {budget_q}"
    if os.environ.get("SAGAREEL_PERF_STRICT_SCANS", "1") == "1":
        assert not scans, f"{name}: quét toàn bảng {scans}"
    return out


def test_clip_list_first_page(big):
    r = measure("clip.list first (200)", big,
                lambda: big["call"]("clip.list", sequence_id=big["big_sequence_id"], limit=200))
    assert len(r["items"]) == 200 and r["next_cursor"]


def test_clip_list_cursor_pages(big):
    def walk():
        seen, cursor = [], None
        while True:
            page = big["call"]("clip.list", sequence_id=big["big_sequence_id"], limit=200, cursor=cursor)
            seen += [c["id"] for c in page["items"]]
            cursor = page["next_cursor"]
            if cursor is None:
                return seen

    seen = measure("clip.list cursor pages (1000)", big, walk)
    assert seen == big["big_clip_ids"]


def test_clip_move_single_row(big):
    ids = big["big_clip_ids"]
    # Mỗi lần đo di chuyển một clip khác nhau để không ra cùng vị trí.
    moves = iter(range(3, 1000, 7))

    def mv():
        i = next(moves)
        return big["call"]("clip.move", ids=[ids[i]], after_clip_id=ids[i - 2])

    measure("clip.move (1 of 1000)", big, mv)


def test_sequence_list_with_counts(big):
    r = measure("sequence.list (200 eps)", big,
                lambda: big["call"]("sequence.list", project_id=big["project_id"], limit=500))
    assert len(r["items"]) == 200


def test_project_get(big):
    r = measure("project.get", big, lambda: big["call"]("project.get", id=big["project_id"]))
    assert r["id"] == big["project_id"]


def test_review_queue(big):
    r = measure("review.queue", big,
                lambda: big["call"]("review.queue", sequence_id=big["big_sequence_id"], limit=200))
    assert r["items"]


def test_sequence_lint(big):
    measure("sequence.lint", big, lambda: big["call"]("sequence.lint", id=big["big_sequence_id"]))


def test_sequence_cost_summary(big):
    r = measure("sequence.cost_summary", big,
                lambda: big["call"]("sequence.cost_summary", id=big["big_sequence_id"], per_clip=True))
    assert r


def test_pipeline_status_global(big):
    measure("pipeline.status global", big, lambda: big["call"]("pipeline.status", limit=200))


def test_history_list(big):
    r = measure("history.list", big, lambda: big["call"]("history.list", project_id=big["project_id"], limit=50))
    assert r["items"]


def test_job_list(big):
    r = measure("job.list (50)", big, lambda: big["call"]("job.list", project_id=big["project_id"], limit=50))
    assert len(r["items"]) == 50


def test_bible_search(big):
    r = measure("bible.search", big, lambda: big["call"]("bible.search", project_id=big["project_id"], q="cang"))
    assert r["items"]


def test_layout_compute_1000_clips(big):
    from sagareel.render import layout, pipeline

    def run():
        loaded = pipeline.load(big["services"], big["big_sequence_id"], "draft")
        return layout.compute(loaded.seq, transitions=loaded.transitions, fps=loaded.profile.fps)

    tl = measure("layout (load + compute, 1000 clips)", big, run)
    assert len(tl.clips) == 1000


def test_import_preview_1000_rows(big):
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Tiêu đề", "Prompt ảnh", "Thoại", "Thời lượng"])
    for i in range(1000):
        w.writerow([f"Clip {i}", f"Cảnh số {i}", f"NR: câu thoại {i}\nNR: câu hai {i}", 3 + i % 4])
    csv_text = buf.getvalue()
    r = measure("import.preview (1000 rows)", big, lambda: big["call"](
        "import.preview", sequence_id=big["sequence_ids"][1], source={"kind": "csv", "text": csv_text}), repeat=2)
    assert r["summary"]


def test_export_spec_big_episode(big):
    r = measure("project.export_spec (200 eps, 5k clips)", big, lambda: big["call"](
        "project.export_spec", project_id=big["project_id"], include=["sequences"], save=False), repeat=2)
    assert r["yaml"]


def test_sse_payload_bulk_update_1000_clips(big):
    """Kích thước `entity.changed` khi sửa hàng loạt 1000 clip (chỉ đo + in; ghi vào docs/perf/m8-scale.md)."""
    seen = []
    orig = big["services"].bus.publish

    def spy(kind, data, **kw):
        seen.append((kind, data))
        return orig(kind, data, **kw)

    big["services"].bus.publish = spy
    try:
        big["call"]("clip.bulk_update", ids=big["big_clip_ids"], values={"notes": "sse"})
    finally:
        big["services"].bus.publish = orig
    ev = [d for k, d in seen if k == "entity.changed"]
    size = max(len(json.dumps(d, separators=(",", ":"))) for d in ev)
    print(f"\n[perf] entity.changed bulk 1000: {size} bytes")
    RESULTS["_sse_bytes"] = {"bytes": size}
    assert size < 64 * 1024, f"entity.changed {size} byte > 64 KB: cần nén danh sách entities"
    path = os.environ.get("SAGAREEL_PERF_JSON")
    if path:
        Path(path).write_text(json.dumps(RESULTS, ensure_ascii=False, indent=1), encoding="utf-8")
