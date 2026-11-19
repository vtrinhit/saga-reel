"""Layout thời gian kiểu "magnetic" (spec §23.1) — hàm thuần, không đọc DB, không chạy ffmpeg.

- Track video là chuỗi nối liền theo thứ tự clip: `start_i = start_{i-1} + dur_{i-1} − overlap_i`; không khoảng trống.
- Chuyển cảnh có thời lượng (`fade`, `fadeblack`…) làm hai clip chồng nhau `overlap_i` và rút ngắn tổng tương ứng;
  `cut` (và `continue`) chồng 0.
- Chuyển cảnh của clip i là chỗ nối (i−1 → i): `clip.transition.name` nếu có, không thì `link=fade` → `fade`, còn lại cắt.
  Thời lượng: `transition.duration_ms`, không thì của preset (sang cảnh khác dùng `new_scene_duration_ms`); luôn
  ≤ nửa clip ngắn hơn (để clip còn phần "sạch") và làm tròn về khung.
- Thoại neo trong clip (`offset_ms`); nhạc/SFX/title neo vào clip (`anchor_clip_id + offset_ms`), kết thúc theo
  `duration_ms`, `end_anchor` (`{clip_id}` = hết clip đó, `{sequence_end: true}` = hết tập) hoặc độ dài media.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DEFAULT_TRANSITIONS: dict[str, dict[str, Any]] = {
    "cut": {"xfade": None, "duration_ms": 0},
    "fade": {"xfade": "fade", "duration_ms": 500, "new_scene_duration_ms": 700},
    "dissolve": {"xfade": "dissolve", "duration_ms": 500, "new_scene_duration_ms": 700},
    "fadeblack": {"xfade": "fadeblack", "duration_ms": 1000, "new_scene_duration_ms": 1000},
    "fadewhite": {"xfade": "fadewhite", "duration_ms": 700, "new_scene_duration_ms": 700},
}


# --- Đầu vào ------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ClipIn:
    id: int
    dur_ms: int
    key: str = ""
    scene_id: int | None = None
    link: str = "cut"
    transition: dict[str, Any] = field(default_factory=dict)
    src: dict[str, Any] = field(default_factory=dict)  # vật liệu dựng/fingerprint, layout không đọc


@dataclass(frozen=True)
class LineIn:
    id: int
    clip_id: int
    offset_ms: int = 0
    duration_ms: int = 0
    text: str = ""
    subtitle: bool = True
    src: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ItemIn:
    id: int
    track: str  # music/sfx/title
    anchor_clip_id: int | None = None
    offset_ms: int = 0
    duration_ms: int | None = None
    end_anchor: dict[str, Any] | None = None
    media_duration_ms: int | None = None
    params: dict[str, Any] = field(default_factory=dict)
    src: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SequenceIn:
    clips: tuple[ClipIn, ...] | list[ClipIn]
    lines: tuple[LineIn, ...] | list[LineIn] = ()
    items: tuple[ItemIn, ...] | list[ItemIn] = ()


# --- Kết quả ------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ClipSlot:
    id: int
    index: int
    start_ms: int
    dur_ms: int
    overlap_ms: int  # chồng với clip trước (chuyển cảnh vào)
    transition: str  # "cut" hoặc tên preset transition
    xfade: str | None  # tên transition của filter xfade
    scene_id: int | None = None
    key: str = ""
    src: dict[str, Any] = field(default_factory=dict)

    @property
    def end_ms(self) -> int:
        return self.start_ms + self.dur_ms


@dataclass(frozen=True)
class Placed:
    id: int
    kind: str  # dialogue/music/sfx/title
    start_ms: int
    end_ms: int
    clip_id: int | None = None
    text: str = ""
    subtitle: bool = False
    params: dict[str, Any] = field(default_factory=dict)
    src: dict[str, Any] = field(default_factory=dict)

    @property
    def dur_ms(self) -> int:
        return self.end_ms - self.start_ms


@dataclass(frozen=True)
class Timeline:
    clips: list[ClipSlot]
    dialogue: list[Placed]
    sfx: list[Placed]
    music: list[Placed]
    titles: list[Placed]
    total_ms: int
    warnings: list[dict[str, Any]] = field(default_factory=list)

    def clip(self, clip_id: int) -> ClipSlot | None:
        return next((c for c in self.clips if c.id == clip_id), None)


# --- Tính ---------------------------------------------------------------------------------------------------------


def _floor_frames(ms: float, fps: int | None) -> int:
    """Làm tròn xuống số khung nguyên (ms); `fps` None → giữ nguyên (int)."""
    if not fps:
        return int(ms)
    return round(int(ms * fps / 1000 + 1e-9) * 1000 / fps)


def resolve_transition(
    prev: ClipIn, cur: ClipIn, transitions: dict[str, dict[str, Any]], warnings: list[dict[str, Any]]
) -> tuple[str, str | None, int]:
    """(tên preset, tên xfade, thời lượng ms chưa giới hạn) cho chỗ nối prev → cur."""
    t = cur.transition or {}
    name = str(t.get("name") or ("fade" if cur.link == "fade" else "cut"))
    if name == "cut":
        return "cut", None, 0
    preset = transitions.get(name)
    if preset is None or not preset.get("xfade"):
        warnings.append({"code": "transition_unknown", "clip_id": cur.id, "name": name,
                         "message": f"Chuyển cảnh “{name}” không có; dùng cắt thẳng."})
        return "cut", None, 0
    d = t.get("duration_ms")
    if d is None:
        new_scene = prev.scene_id != cur.scene_id
        d = preset.get("new_scene_duration_ms") if new_scene else None
        d = d if d is not None else preset.get("duration_ms", 500)
    return name, str(preset["xfade"]), max(0, int(d))


def compute(
    seq: SequenceIn,
    *,
    transitions: dict[str, dict[str, Any]] | None = None,
    fps: int | None = 30,
) -> Timeline:
    """Timeline tuyệt đối của một sequence. `fps` (None = không làm tròn) làm tròn độ chồng về khung."""
    table = transitions if transitions is not None else DEFAULT_TRANSITIONS
    warnings: list[dict[str, Any]] = []

    slots: list[ClipSlot] = []
    cursor = 0
    prev_in: ClipIn | None = None
    for i, c in enumerate(seq.clips):
        dur = max(0, int(c.dur_ms))
        name, xf, overlap = "cut", None, 0
        if prev_in is not None:
            name, xf, want = resolve_transition(prev_in, c, table, warnings)
            if want:
                limit = min(slots[-1].dur_ms, dur) // 2
                overlap = _floor_frames(min(want, limit), fps)
                if want > limit:
                    warnings.append({"code": "transition_clamped", "clip_id": c.id, "wanted_ms": want,
                                     "used_ms": overlap,
                                     "message": "Chuyển cảnh dài hơn nửa clip ngắn hơn nên đã được rút ngắn."})
                if overlap <= 0:
                    name, xf, overlap = "cut", None, 0
        start = cursor - overlap
        slots.append(ClipSlot(id=c.id, index=i, start_ms=start, dur_ms=dur, overlap_ms=overlap, transition=name,
                              xfade=xf, scene_id=c.scene_id, key=c.key, src=dict(c.src)))
        cursor = start + dur
        prev_in = c
    total = cursor
    by_id = {s.id: s for s in slots}

    dialogue: list[Placed] = []
    for ln in seq.lines:
        slot = by_id.get(ln.clip_id)
        if slot is None:
            warnings.append({"code": "dialogue_orphan", "line_id": ln.id,
                             "message": "Câu thoại thuộc clip không có trong tập; bỏ qua."})
            continue
        start = slot.start_ms + int(ln.offset_ms)
        end = start + max(0, int(ln.duration_ms))
        if end > total:
            warnings.append({"code": "dialogue_overflow", "line_id": ln.id,
                             "message": "Câu thoại kéo quá cuối tập; phần thừa bị cắt."})
        dialogue.append(Placed(id=ln.id, kind="dialogue", start_ms=start, end_ms=min(end, total) if total else end,
                               clip_id=ln.clip_id, text=ln.text, subtitle=ln.subtitle, src=dict(ln.src)))
    dialogue.sort(key=lambda p: (p.start_ms, p.id))

    tracks: dict[str, list[Placed]] = {"music": [], "sfx": [], "title": []}
    for it in seq.items:
        if it.track not in tracks:
            warnings.append({"code": "track_unknown", "item_id": it.id, "message": f"Track “{it.track}” không hợp lệ."})
            continue
        base = 0
        anchor = by_id.get(it.anchor_clip_id) if it.anchor_clip_id is not None else None
        if it.anchor_clip_id is not None and anchor is None:
            warnings.append({"code": "anchor_missing", "item_id": it.id,
                             "message": "Clip neo không còn trong tập; item được đặt từ đầu tập."})
        if anchor is not None:
            base = anchor.start_ms
        start = max(0, base + int(it.offset_ms))
        end: int | None = None
        if it.duration_ms is not None:
            end = start + max(0, int(it.duration_ms))
        elif it.end_anchor:
            ea = it.end_anchor
            if ea.get("sequence_end"):
                end = total
            elif ea.get("clip_id") is not None and ea["clip_id"] in by_id:
                end = by_id[ea["clip_id"]].end_ms
        if end is None:
            if it.track == "music" and (it.params.get("loop") or not it.media_duration_ms):
                end = total
            elif it.media_duration_ms:
                end = start + int(it.media_duration_ms)
            elif anchor is not None:
                end = anchor.end_ms
            else:
                end = total
        end = min(end, total)
        if end <= start:
            warnings.append({"code": "item_empty", "item_id": it.id,
                             "message": "Item nằm ngoài thời lượng tập; bỏ qua."})
            continue
        tracks[it.track].append(Placed(id=it.id, kind=it.track, start_ms=start, end_ms=end,
                                       clip_id=it.anchor_clip_id, text=str(it.params.get("text", "")),
                                       params=dict(it.params), src=dict(it.src)))
    for lst in tracks.values():
        lst.sort(key=lambda p: (p.start_ms, p.id))

    return Timeline(clips=slots, dialogue=dialogue, sfx=tracks["sfx"], music=tracks["music"],
                    titles=tracks["title"], total_ms=total, warnings=warnings)
