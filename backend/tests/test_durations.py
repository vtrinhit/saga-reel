"""Thời lượng clip tự do + mức giây provider lấy từ caps của kết nối (không danh sách cứng)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sagareel.core import durations
from sagareel.providers import registry
from sagareel.providers.base import Connection

ROOT = Path(__file__).resolve().parents[2]


def test_pick_smallest_supported_at_least_desired_plus_pad():
    caps = [3, 5, 6, 7, 8, 10, 12, 15]
    assert durations.pick(2000, caps) == (3, None)
    assert durations.pick(2700, caps) == (3, None)  # 2,7 + 0,3 = 3,0
    assert durations.pick(2701, caps) == (5, None)
    assert durations.pick(6000, caps) == (7, None)
    sec, warn = durations.pick(15_000, caps)
    assert sec == 15 and "vượt" in warn
    assert durations.pick(4200, []) == (5, None)  # không caps → làm tròn lên giây
    assert durations.pick(4200, "5, 10") == (5, None)  # form gửi chuỗi


def test_exact_override_keeps_supported_seconds():
    assert durations.exact(6, [3, 6, 10]) == (6, None)
    sec, warn = durations.exact(7, [3, 6, 10])
    assert sec == 10 and warn
    assert durations.exact(9, []) == (9, None)


def test_sanitize():
    assert durations.sanitize([10, "5", 5, 0, -3, 2.5, True, "x"]) == [5, 10]
    assert durations.sanitize("3;5 8") == [3, 5, 8]
    assert durations.sanitize(None) == []


def _conn(adapter: str, options: dict) -> Connection:
    return Connection(id=1, name="c", adapter=adapter, kind="video", base_url="http://127.0.0.1:1", api_key="",
                      default_model="", options=options, concurrency=1)


def test_multikit_caps_default_and_connection_override():
    cls = registry.get("multikit")
    assert cls(_conn("multikit", {})).capabilities()["durations"] == [3, 5, 6, 7, 8, 10, 12, 15]
    custom = cls(_conn("multikit", {"durations": [4, 9]}))
    assert custom.capabilities()["durations"] == [4, 9]
    assert durations.pick(4000, custom.capabilities()["durations"]) == (9, None)
    assert "durations" in {f.name for f in cls.config_fields}


def test_regenerate_snaps_to_connection_caps(client):
    """Kết nối fake_video với `options.durations` riêng → dry_run xin mức ≥ mong muốn + 0,3 s trong caps đó."""
    def ok(op: str, body: dict) -> dict:
        r = client.post(f"/api/ops/{op}", json=body)
        assert r.status_code == 200, r.text
        return r.json()

    r = client.post("/api/ops/provider.create", json={"kind": "video", "adapter": "fake_video", "name": "vid-caps",
                                                       "options": {"durations": [4, 9]}})
    assert r.status_code == 200, r.text
    pid = ok("project.create", {"name": "P"})["id"]
    seq = ok("sequence.create", {"project_id": pid, "title": "T"})["id"]
    a = ok("clip.create", {"sequence_id": seq, "fields": {"duration_ms": 3500, "motion_prompt": "x"}})
    b = ok("clip.create", {"sequence_id": seq, "fields": {"duration_ms": 3800, "motion_prompt": "x"}})
    c = ok("clip.create", {"sequence_id": seq, "fields": {"duration_ms": 12_000, "motion_prompt": "x"}})
    out = ok("clip.regenerate", {"ids": [a["id"], b["id"], c["id"]], "part": "video", "dry_run": True,
                                         "overrides": {"connection": "vid-caps"}})
    assert out["estimate"]["parts"]["video"]["jobs"] == 3
    assert any("vượt" in w for w in out["warnings"])  # 12 s > 9 s


def test_no_hardcoded_duration_list_in_code():
    """Không còn danh sách 6/10/15 cứng trong code (chỉ caps của kết nối)."""
    pat = re.compile(r"[\[(]\s*6\s*,\s*10\s*,\s*15\s*[\])]|\b6\s*/\s*10\s*/\s*15\b")
    dirs = [ROOT / "backend" / "sagareel", ROOT / "frontend" / "src", ROOT / "mcp_server" / "sagareel_mcp"]
    hits = []
    for d in dirs:
        if not d.is_dir():
            continue
        for p in d.rglob("*"):
            if p.suffix not in (".py", ".yaml", ".ts", ".tsx", ".md", ".j2") or "node_modules" in p.parts:
                continue
            if p.name == "schema.d.ts":
                continue
            text = p.read_text("utf-8", errors="replace")
            hits += [f"{p.relative_to(ROOT)}:{text[:m.start()].count(chr(10)) + 1}" for m in pat.finditer(text)]
    assert hits == []


@pytest.mark.parametrize("adapter", ["fake_video", "multikit"])
def test_video_adapters_declare_durations_field(adapter):
    cls = registry.get(adapter)
    assert "durations" in cls.default_capabilities
