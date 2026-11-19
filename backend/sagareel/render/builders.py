"""Chỗ nối duy nhất với Task D (`sagareel.services.audio.subtitles|mix`) — dựng phim gọi qua `Builders`.

Mặc định import muộn đúng tên trong hợp đồng: `subtitles.build_cues`, `subtitles.to_ass`, `mix.final_audio_filter`.
Chỉ import khi thật sự cần (có câu phụ đề / có nhạc nền): tập không phụ đề, không nhạc dựng được khi chưa có Task D.
Test (và người điều phối khi khớp chữ ký thật của D) truyền `Builders(...)` riêng.

Quy ước gọi (Task E):
- `build_cues(lines_with_timing, style, width, height, safe_zone) -> list[Cue]` với `lines_with_timing` =
  `[{id, text, start_ms, end_ms, words?, lang}]` (thời gian **cục bộ của khối**), `style` = dict preset subtitle_style.
- `to_ass(cues, style, width, height) -> str` (nội dung file ASS, UTF-8).
- `final_audio_filter(dialogue_stem, music_items, sfx, params) -> (filter_complex, maps)`:
  `dialogue_stem` = nhãn input stem thoại (vd. `"1:a"`), `music_items` = `[{input, start_ms, end_ms, params}]`
  (`input` = nhãn input, vd. `"2:a"`), `sfx` = `[]` (SFX đã nằm trong khối), `params` = `{bed: "0:a", total_ms,
  loudnorm: False, ...}`. Kết quả: filter_complex (chuỗi) và `maps` (danh sách nhãn đầu ra, phần tử đầu là âm trộn,
  vd. `["[mix]"]`). Task E tự làm loudnorm hai lượt sau bước trộn này.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sagareel.core.errors import AppError


def _not_ready(what: str) -> AppError:
    return AppError(
        "not_implemented",
        f"Chưa có module {what} (Task D) nên không dựng được phần này.",
        hint="Tắt phụ đề/nhạc nền cho lần dựng này hoặc chờ module âm thanh.",
        status=501,
    )


def _subtitles() -> Any:
    try:
        from sagareel.services.audio import subtitles
    except ImportError:
        raise _not_ready("phụ đề sagareel.services.audio.subtitles") from None
    return subtitles


def _mix() -> Any:
    try:
        from sagareel.services.audio import mix
    except ImportError:
        raise _not_ready("trộn âm sagareel.services.audio.mix") from None
    return mix


def default_build_cues(lines_with_timing, style, width, height, safe_zone):
    return _subtitles().build_cues(lines_with_timing, style, width, height, safe_zone)


def default_to_ass(cues, style, width, height):
    return _subtitles().to_ass(cues, style, width, height)


def default_final_audio_filter(dialogue_stem, music_items, sfx, params):
    return _mix().final_audio_filter(dialogue_stem, music_items, sfx, params)


@dataclass(frozen=True)
class Builders:
    build_cues: Callable[..., list] = default_build_cues
    to_ass: Callable[..., str] = default_to_ass
    final_audio_filter: Callable[..., tuple[str, list[str]]] = default_final_audio_filter


DEFAULT = Builders()
