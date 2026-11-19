"""Hồ sơ dựng: kích thước khung, fps, tham số mã hoá (post-production.md §5 + "Mặc định đề xuất" › encode).

- Kích thước = tỉ lệ khung project (`9:16`…) × độ phân giải (`1080p` = cạnh ngắn 1080). Nháp/animatic: cạnh ngắn
  `min(540, cạnh ngắn project)`.
- Segment trung gian (`.mov`): H.264 chất lượng cao + PCM 48 kHz stereo (ghép không mất mát, độ dài chính xác).
- Khối: H.264 theo biến thể/nền tảng, âm PCM; lượt âm thanh cuối mới mã hoá AAC theo nền tảng.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

from sagareel.core.errors import AppError

FPS = 30
DRAFT_SHORT_SIDE = 540

COLOR_TAGS = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]
VIDEO_COMMON = ["-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p", "-bf", "2", "-sc_threshold", "0"]
AUDIO_PCM = ["-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2"]

# encode.draft / shorts_reels_tiktok / yt_long của post-production.md (GOP nháp thêm cho đều khối).
ENCODE: dict[str, list[str]] = {
    "draft": ["-preset", "veryfast", "-crf", "26", "-g", "60", "-keyint_min", "60"],
    "shorts": ["-preset", "slow", "-crf", "18", "-maxrate", "10M", "-bufsize", "20M", "-g", "60", "-keyint_min", "60"],
    "yt_long": ["-preset", "slow", "-crf", "17", "-maxrate", "12M", "-bufsize", "24M", "-g", "15",
                "-keyint_min", "15", "-x264-params", "open-gop=0"],
}
AUDIO_BITRATE = {"draft": "128k", "shorts": "192k", "yt_long": "384k"}
PLATFORM_ENCODE = {"yt-long": "yt_long"}  # còn lại (fb-reels, fb-video, yt-shorts, tiktok, rỗng) → shorts
INTERMEDIATE = {
    "draft": ["-preset", "veryfast", "-crf", "18", "-g", "30"],
    "final": ["-preset", "fast", "-crf", "12", "-g", "30"],
}

GRADE_BASE = "eq=contrast=1.04:saturation=1.06,unsharp=5:5:0.4:5:5:0.0"

VARIANTS = ("draft", "animatic", "final")


def _even(n: float) -> int:
    return max(2, round(n / 2) * 2)


def parse_short_side(resolution: str) -> int:
    r = (resolution or "").strip().lower()
    if r in ("4k", "uhd"):
        return 2160
    m = re.fullmatch(r"(\d{2,4})p?", r)
    if not m:
        raise AppError("render_bad_resolution", f"Độ phân giải “{resolution}” không hợp lệ (vd. 1080p).", status=422)
    return int(m.group(1))


def parse_aspect(aspect: str) -> tuple[int, int]:
    m = re.fullmatch(r"\s*(\d+)\s*[:x/]\s*(\d+)\s*", aspect or "")
    if not m or int(m.group(1)) <= 0 or int(m.group(2)) <= 0:
        raise AppError("render_bad_aspect", f"Tỉ lệ khung “{aspect}” không hợp lệ (vd. 9:16).", status=422)
    return int(m.group(1)), int(m.group(2))


def dims(aspect: str, resolution: str | int) -> tuple[int, int]:
    """(W, H) chẵn: cạnh ngắn = độ phân giải."""
    short = resolution if isinstance(resolution, int) else parse_short_side(resolution)
    a, b = parse_aspect(aspect)
    if a <= b:
        return _even(short), _even(short * b / a)
    return _even(short * a / b), _even(short)


@dataclass(frozen=True)
class Profile:
    width: int
    height: int
    fps: int = FPS
    variant: str = "draft"
    encode: str = "draft"  # khoá trong ENCODE
    grade: bool = True
    lut: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def tier(self) -> str:
        """Bậc trung gian: nháp/animatic dùng `draft`, bản cuối `final`."""
        return "final" if self.variant == "final" else "draft"

    def key(self) -> dict[str, Any]:
        """Phần đưa vào fingerprint (không chứa đường dẫn máy)."""
        d = asdict(self)
        d["encode_args"] = ENCODE[self.encode]
        return d

    def video_args(self) -> list[str]:
        return [*VIDEO_COMMON, *ENCODE[self.encode], *COLOR_TAGS]

    def intermediate_args(self) -> list[str]:
        return [*VIDEO_COMMON, *INTERMEDIATE[self.tier], *COLOR_TAGS, *AUDIO_PCM]

    @property
    def audio_bitrate(self) -> str:
        return AUDIO_BITRATE[self.encode]

    def frames(self, ms: float) -> int:
        return max(1, round(ms * self.fps / 1000))

    def frame_ms(self, ms: float) -> int:
        """`ms` làm tròn về số khung nguyên (đơn vị ms)."""
        return round(self.frames(ms) * 1000 / self.fps)


def for_render(aspect: str, resolution: str, variant: str = "draft", platform: str | None = None) -> Profile:
    if variant not in VARIANTS:
        raise AppError("render_bad_variant", f"Biến thể “{variant}” không hợp lệ (draft/animatic/final).", status=422)
    short = parse_short_side(resolution)
    if variant == "final":
        w, h = dims(aspect, short)
        return Profile(w, h, variant="final", encode=PLATFORM_ENCODE.get(platform or "", "shorts"))
    w, h = dims(aspect, min(DRAFT_SHORT_SIDE, short))
    return Profile(w, h, variant=variant, encode="draft")


def coerce(profile: Profile | Mapping[str, Any]) -> Profile:
    """Nhận `Profile` hoặc mapping `{width, height, fps?, variant?, ...}` (Task B gọi still_motion bằng dict)."""
    if isinstance(profile, Profile):
        return profile
    if isinstance(profile, Mapping):
        allowed = {"width", "height", "fps", "variant", "encode", "grade", "lut", "extra"}
        data = {k: v for k, v in profile.items() if k in allowed}
        if "width" not in data or "height" not in data:
            if "aspect" in profile:
                w, h = dims(str(profile["aspect"]), str(profile.get("resolution", "1080p")))
                data.setdefault("width", w)
                data.setdefault("height", h)
            else:
                raise AppError("render_bad_profile", "Hồ sơ dựng thiếu width/height.", status=422)
        data["width"], data["height"] = _even(int(data["width"])), _even(int(data["height"]))
        return Profile(**data)
    raise AppError("render_bad_profile", "Hồ sơ dựng không hợp lệ.", status=422)
