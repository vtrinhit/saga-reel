"""Sinh `layout-fixture.json` bằng chính `sagareel.render.layout.compute` (backend) cho `lib/timeline.test.ts`.

Chạy (PowerShell, từ gốc worktree):
    $env:PYTHONPATH = "<worktree>\\backend"
    D:\\MMO\\SagaReel_Studio\\.venv\\Scripts\\python frontend\\src\\lib\\__fixtures__\\gen_layout_fixture.py

Mỗi ca: đầu vào (dạng JSON khớp `SequenceIn`), bảng transition (null = DEFAULT_TRANSITIONS), fps, và kết quả backend.
`end_ms`/`dur_ms` là property nên thêm tay. Sửa `layout.py` → chạy lại script này → test frontend báo lệch nếu có.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from sagareel.render.layout import ClipIn, ItemIn, LineIn, SequenceIn, compute
from sagareel.render.profile import Profile

OUT = Path(__file__).with_name("layout-fixture.json")

CUSTOM_TRANSITIONS: dict[str, dict[str, Any]] = {
    "cut": {"xfade": None, "duration_ms": 0},
    "fade": {"xfade": "fade", "duration_ms": 400, "new_scene_duration_ms": 650},
    "wipe": {"xfade": "wipeleft", "duration_ms": 333},
    "broken": {"xfade": None, "duration_ms": 500},
    "nodefault": {"xfade": "slideup"},
}


def clip(id: int, dur: int, scene: int | None = 1, link: str = "cut", transition: dict | None = None) -> dict:
    return {"id": id, "dur_ms": dur, "key": f"C{id}", "scene_id": scene, "link": link, "transition": transition or {}}


def line(id: int, clip_id: int, offset: int = 0, dur: int = 1000, text: str = "", subtitle: bool = True) -> dict:
    return {"id": id, "clip_id": clip_id, "offset_ms": offset, "duration_ms": dur, "text": text, "subtitle": subtitle}


def item(id: int, track: str, anchor: int | None = None, offset: int = 0, duration: int | None = None,
         end_anchor: dict | None = None, media: int | None = None, params: dict | None = None) -> dict:
    return {"id": id, "track": track, "anchor_clip_id": anchor, "offset_ms": offset, "duration_ms": duration,
            "end_anchor": end_anchor, "media_duration_ms": media, "params": params or {}}


CASES: list[dict[str, Any]] = [
    {"name": "trống", "clips": []},
    {"name": "một clip, không làm tròn", "clips": [clip(1, 4000)], "fps": None},
    {"name": "cắt thẳng 3 clip", "clips": [clip(1, 4000), clip(2, 3000), clip(3, 5000)]},
    {"name": "link=fade cùng cảnh và sang cảnh mới",
     "clips": [clip(1, 4000, 1), clip(2, 4000, 1, "fade"), clip(3, 4000, 2, "fade"), clip(4, 4000, 2, "continue")]},
    {"name": "scene None == None là cùng cảnh",
     "clips": [clip(1, 4000, None), clip(2, 4000, None, "fade"), clip(3, 4000, 5, "fade")]},
    {"name": "transition tên + duration riêng",
     "clips": [clip(1, 6000), clip(2, 6000, transition={"name": "fadeblack"}),
               clip(3, 6000, transition={"name": "dissolve", "duration_ms": 250}),
               clip(4, 6000, 3, transition={"name": "fadewhite"})]},
    {"name": "duration_ms 0 tường minh giữ tên fade, chồng 0",
     "clips": [clip(1, 3000), clip(2, 3000, transition={"name": "fade", "duration_ms": 0})]},
    {"name": "transition name rỗng + link fade",
     "clips": [clip(1, 3000), clip(2, 3000, link="fade", transition={"name": ""})]},
    {"name": "transition cut tường minh thắng link fade",
     "clips": [clip(1, 3000), clip(2, 3000, link="fade", transition={"name": "cut"})]},
    {"name": "transition không có",
     "clips": [clip(1, 3000), clip(2, 3000, transition={"name": "starwipe"})]},
    {"name": "rút ngắn vì nửa clip ngắn hơn",
     "clips": [clip(1, 900), clip(2, 5000, transition={"name": "fadeblack"}), clip(3, 1001, link="fade")]},
    {"name": "rút ngắn về 0 thành cắt",
     "clips": [clip(1, 40), clip(2, 5000, transition={"name": "fade"})]},
    {"name": "clip 0 ms và âm",
     "clips": [clip(1, 0), clip(2, -500, link="fade"), clip(3, 3000, link="fade")]},
    {"name": "fps 24 làm tròn chồng",
     "clips": [clip(1, 5000), clip(2, 5000, transition={"name": "fade", "duration_ms": 523}),
               clip(3, 5000, 2, link="fade")], "fps": 24},
    {"name": "fps 16 chạm .5 (banker)",
     "clips": [clip(1, 5000), clip(2, 5000, transition={"name": "fade", "duration_ms": 437}),
               clip(3, 5000, transition={"name": "fade", "duration_ms": 563}),
               clip(4, 5000, transition={"name": "fade", "duration_ms": 312})], "fps": 16},
    {"name": "fps 48 lẻ",
     "clips": [clip(1, 4000), clip(2, 4000, transition={"name": "fade", "duration_ms": 510}),
               clip(3, 4000, transition={"name": "fade", "duration_ms": 1010})], "fps": 48},
    {"name": "bảng transition tuỳ biến",
     "clips": [clip(1, 4000), clip(2, 4000, link="fade"), clip(3, 4000, 2, link="fade"),
               clip(4, 4000, 2, transition={"name": "wipe"}), clip(5, 4000, 2, transition={"name": "broken"}),
               clip(6, 4000, 3, transition={"name": "nodefault"}), clip(7, 4000, 3, transition={"name": "dissolve"})],
     "transitions": CUSTOM_TRANSITIONS},
    {"name": "thoại: offset, mồ côi, tràn cuối, sắp xếp",
     "clips": [clip(1, 4000), clip(2, 4000, link="fade")],
     "lines": [line(5, 2, 200, 1500, "b"), line(3, 1, 0, 1200, "a"), line(4, 1, 0, 800, "a2", False),
               line(9, 99, 0, 1000, "mồ côi"), line(7, 2, 3000, 4000, "tràn"), line(8, 1, 500, -100, "âm")]},
    {"name": "thoại khi tập trống", "clips": [], "lines": [line(1, 1, 0, 1000, "x")]},
    {"name": "nhạc/SFX/title đủ nhánh",
     "clips": [clip(1, 4000), clip(2, 6000, link="fade"), clip(3, 5000, 2, link="fade")],
     "items": [
         item(1, "music", None, 0, None, None, None, {"volume_db": -12}),
         item(2, "music", 2, 500, None, None, 3000),
         item(3, "music", 2, 0, None, None, 3000, {"loop": True}),
         item(4, "sfx", 1, 1000, 800),
         item(5, "sfx", 2, 0, None, {"clip_id": 3}),
         item(6, "sfx", 3, 0, None, {"sequence_end": True}),
         item(7, "sfx", 3, 0, None, {"clip_id": 999}, 2000),
         item(8, "sfx", 3, 0, None, {"clip_id": 999}),
         item(9, "sfx", None, 2000),
         item(10, "sfx", 404, 100, 500),
         item(11, "title", 1, 0, 2000, None, None, {"text": "Tập 1"}),
         item(12, "title", 2, 0, None, None, None, {"text": None}),
         item(13, "title", 3, 0, None, None, None),
         item(14, "voice", 1, 0, 1000),
         item(15, "sfx", 3, 9000, 1000),
         item(16, "music", 3, 4000, 5000),
         item(17, "sfx", 1, 0, None, {}, 1500),
         item(18, "sfx", 1, 0, None, {"clip_id": None}),
         item(19, "music", None, 0, None, None, 0),
         item(20, "sfx", 2, 0, 0),
     ]},
]


def to_in(case: dict[str, Any]) -> SequenceIn:
    return SequenceIn(
        clips=[ClipIn(**c) for c in case.get("clips", [])],
        lines=[LineIn(**ln) for ln in case.get("lines", [])],
        items=[ItemIn(**it) for it in case.get("items", [])],
    )


def slot_dict(x: Any) -> dict[str, Any]:
    d = asdict(x)
    d.pop("src", None)
    d["end_ms"] = x.end_ms
    return d


def placed_dict(x: Any) -> dict[str, Any]:
    d = asdict(x)
    d.pop("src", None)
    d["dur_ms"] = x.dur_ms
    return d


def main() -> None:
    out = []
    for case in CASES:
        fps = case.get("fps", 30)
        tl = compute(to_in(case), transitions=case.get("transitions"), fps=fps)
        out.append({
            "name": case["name"],
            "fps": fps,
            "transitions": case.get("transitions"),
            "input": {"clips": case.get("clips", []), "lines": case.get("lines", []), "items": case.get("items", [])},
            "output": {
                "clips": [slot_dict(s) for s in tl.clips],
                "dialogue": [placed_dict(p) for p in tl.dialogue],
                "sfx": [placed_dict(p) for p in tl.sfx],
                "music": [placed_dict(p) for p in tl.music],
                "titles": [placed_dict(p) for p in tl.titles],
                "total_ms": tl.total_ms,
                "warnings": tl.warnings,
            },
        })
    # `Profile.frame_ms` (thời lượng clip ảnh/khung đen trước khi vào layout): round() kiểu Python, chạm .5 ở 30 fps.
    prof = Profile(1080, 1920)
    frame_ms = [[ms, prof.frame_ms(ms)] for ms in
                (0, 1, 16, 17, 33, 34, 50, 4950, 4983, 5000, 5016, 5017, 6000, 7450, 12345)]
    doc = {"layout": out, "frame_ms": frame_ms}
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(out)} ca → {OUT}")


if __name__ == "__main__":
    main()
