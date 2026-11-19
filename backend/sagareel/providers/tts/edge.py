"""`edge_tts`: đọc lời bằng Microsoft Edge TTS (thư viện `edge-tts`, miễn phí, không cần khoá/URL).

- Giọng: `edge_tts.list_voices()`, lưu `cache_dir/edge_voices.json` 1 ngày (mất mạng thì dùng bản cũ nếu có).
- Đọc: `Communicate(text, voice, rate, pitch, volume, boundary="WordBoundary")` — chữ gửi **nguyên văn**; âm thanh
  ghi vào file tạm rồi `os.replace`; thời điểm từng từ lấy từ sự kiện `WordBoundary` (đơn vị 100 ns → ms, chữ chuẩn
  hoá NFC); `duration_ms` đo bằng ffprobe.
- Cache: khoá sha256 của mọi tham số làm đổi âm thanh → `cache_dir/tts/<khoá>.mp3` + `.json` (thời lượng, từ). Trúng
  cache thì không gọi dịch vụ; file kết quả luôn là **bản chép** ra `req.out_path` (người gọi được phép chuyển nó đi).
- Lỗi: `NoAudioReceived` (vi-VN chập chờn phía máy chủ, rany2/edge-tts#512), lỗi WebSocket/mạng →
  `TransientError(retry_after=20·2^attempt, tối đa 300 s)`. Miễn phí nên không cần dấu "submitting".
- Sức khoẻ: `list_voices()` có giọng `vi-VN` → `alive`; lỗi mạng → `dead`.

`cache_dir`: tham số khởi tạo, ngược lại `run.services.settings.cache_dir` của job; không có thì không cache.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

import aiohttp
import anyio
import edge_tts
import httpx
from edge_tts.exceptions import EdgeTTSException, NoAudioReceived

from sagareel.providers import audio_util
from sagareel.providers.base import (
    ConfigField,
    Connection,
    Health,
    ProviderJobError,
    ProviderUnavailable,
    TTSAdapter,
    TTSRequest,
    TTSResult,
)
from sagareel.providers.registry import register_adapter
from sagareel.services.jobs import TransientError

if TYPE_CHECKING:
    from sagareel.services.worker import JobRun

logger = logging.getLogger("sagareel.providers.edge_tts")

BOUNDARY = "WordBoundary"
TICKS_PER_MS = 10_000  # offset/duration của edge-tts tính bằng 100 ns
VOICES_FILE = "edge_voices.json"
VOICES_TTL_S = 86_400
RETRY_BASE_S = 20
RETRY_MAX_S = 300
CACHE_VERSION = 1
# Lỗi mạng/WebSocket/phía máy chủ: thử lại sau. (ValueError/TypeError của tham số được xử lý riêng.)
_TRANSIENT = (EdgeTTSException, aiohttp.ClientError, TimeoutError, ConnectionError)


def retry_after(attempt: int) -> int:
    """20·2^attempt giây, tối đa 300."""
    return min(RETRY_MAX_S, RETRY_BASE_S * 2 ** max(0, int(attempt)))


def cache_key(req: TTSRequest) -> str:
    """sha256 của mọi tham số làm đổi âm thanh (chữ đúng như nhận — không chuẩn hoá)."""
    raw = json.dumps(["edge_tts", CACHE_VERSION, req.text, req.voice, req.rate, req.pitch, req.volume, BOUNDARY],
                     ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def voice_dict(v: dict) -> dict:
    return {
        "id": v.get("ShortName", ""),
        "label": v.get("FriendlyName") or v.get("ShortName", ""),
        "lang": v.get("Locale", ""),
        "gender": str(v.get("Gender", "")).lower(),
    }


def lang_matches(locale: str, lang: str | None) -> bool:
    """`vi` khớp `vi-VN`; `en-US` chỉ khớp `en-US` (không phân biệt hoa thường)."""
    if not lang:
        return True
    locale, lang = locale.lower(), lang.lower()
    return locale == lang if "-" in lang else locale.split("-", 1)[0] == lang


# --- Cache (đồng bộ, chạy trong thread) -------------------------------------------------------------------------------


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _write_json(path: Path, data: Any) -> None:
    audio_util.write_atomic(path, json.dumps(data, ensure_ascii=False).encode("utf-8"))


def _load_cached(tts_dir: Path, key: str, out: Path) -> tuple[int, list[tuple[str, int, int]]] | None:
    """Trúng cache → chép mp3 ra `out`, trả (duration_ms, words). Thiếu/hỏng → None."""
    mp3, meta_path = tts_dir / f"{key}.mp3", tts_dir / f"{key}.json"
    meta = _read_json(meta_path)
    if meta is None or not mp3.is_file():
        return None
    try:
        duration = int(meta["duration_ms"])
        words = [(str(w), int(s), int(e)) for w, s, e in meta["words"]]
    except (KeyError, TypeError, ValueError):
        return None
    audio_util.copy_atomic(mp3, out)
    return duration, words


def _store_cached(tts_dir: Path, key: str, src: Path, duration: int, words: list[tuple[str, int, int]]) -> None:
    """mp3 trước, json sau: có json nghĩa là mp3 đã đủ."""
    audio_util.copy_atomic(src, tts_dir / f"{key}.mp3")
    _write_json(tts_dir / f"{key}.json", {"duration_ms": duration, "words": [list(w) for w in words]})


def _finish(out: Path, audio: bytes, tts_dir: Path | None, key: str,
            words: list[tuple[str, int, int]]) -> int:
    audio_util.write_atomic(out, audio)
    duration = audio_util.duration_ms(out)
    if tts_dir is not None:
        try:
            _store_cached(tts_dir, key, out, duration, words)
        except OSError as e:  # cache hỏng không làm hỏng kết quả
            logger.warning("Không ghi được cache edge-tts %s: %s", key[:12], e)
    return duration


# --- Adapter --------------------------------------------------------------------------------------------------------


@register_adapter
class EdgeTTSAdapter(TTSAdapter):
    adapter_id = "edge_tts"
    label = "Microsoft Edge TTS (miễn phí)"
    config_fields: ClassVar[list[ConfigField]] = []
    default_capabilities: ClassVar[dict] = {"word_timings": True, "cost": 0}

    def __init__(self, conn: Connection, http: httpx.AsyncClient | None = None, *,
                 cache_dir: Path | None = None) -> None:
        super().__init__(conn, http)
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None

    def _cache_root(self, run: JobRun | None) -> Path | None:
        if self.cache_dir is not None:
            return self.cache_dir
        settings = getattr(getattr(run, "services", None), "settings", None)
        root = getattr(settings, "cache_dir", None)
        return Path(root) if root is not None else None

    async def estimate(self, req: TTSRequest) -> dict:
        return {"units": 1, "chars": len(req.text), "cost": 0}

    # --- Giọng / sức khoẻ ---------------------------------------------------------------------------------------

    async def _fetch_voices(self) -> list[dict]:
        voices = [dict(v) for v in await edge_tts.list_voices()]
        if self.cache_dir is not None:
            try:
                await anyio.to_thread.run_sync(
                    _write_json, self.cache_dir / VOICES_FILE, {"fetched_at": time.time(), "voices": voices})
            except OSError as e:
                logger.warning("Không ghi được cache giọng edge-tts: %s", e)
        return voices

    async def _voice_list(self) -> list[dict]:
        cached = None
        if self.cache_dir is not None:
            cached = await anyio.to_thread.run_sync(_read_json, self.cache_dir / VOICES_FILE)
            if cached is not None and not isinstance(cached.get("voices"), list):
                cached = None
            if cached is not None and time.time() - _number(cached.get("fetched_at")) < VOICES_TTL_S:
                return cached["voices"]
        try:
            return await self._fetch_voices()
        except _TRANSIENT as e:
            if cached is not None:  # mất mạng: danh sách cũ vẫn hơn không có
                return cached["voices"]
            raise ProviderUnavailable(
                f"Không lấy được danh sách giọng edge-tts ({type(e).__name__}).",
                hint="Kiểm tra kết nối Internet rồi thử lại.",
            ) from e

    async def voices(self, lang: str | None = None) -> list[dict]:
        return [voice_dict(v) for v in await self._voice_list() if lang_matches(v.get("Locale", ""), lang)]

    async def check_health(self) -> Health:
        try:
            raw = await self._fetch_voices()
        except _TRANSIENT as e:
            return Health(status="dead", detail=f"Không kết nối được dịch vụ edge-tts ({type(e).__name__}).")
        vi = [v.get("ShortName", "") for v in raw if lang_matches(v.get("Locale", ""), "vi")]
        if not vi:
            return Health(status="dead", detail="Dịch vụ edge-tts không còn giọng tiếng Việt (vi-VN).")
        return Health(status="alive", detail=f"edge-tts sẵn sàng, {len(vi)} giọng tiếng Việt.",
                      capabilities={**self.capabilities(), "voices_vi": vi, "word_timings": True})

    # --- Đọc ----------------------------------------------------------------------------------------------------

    async def synthesize(self, req: TTSRequest, run: JobRun | None = None) -> TTSResult:
        out = Path(req.out_path)
        key = cache_key(req)
        root = self._cache_root(run)
        tts_dir = root / "tts" if root is not None else None

        if tts_dir is not None:
            hit = await anyio.to_thread.run_sync(_load_cached, tts_dir, key, out)
            if hit is not None:
                if run is not None:
                    run.log("info", "Dùng lại âm thanh đã đọc trước đó (cache edge-tts).")
                duration, words = hit
                return TTSResult(path=out, duration_ms=duration, words=words if req.word_timings else [])

        try:
            comm = edge_tts.Communicate(req.text, req.voice, rate=req.rate, volume=req.volume, pitch=req.pitch,
                                        boundary=BOUNDARY)
        except (ValueError, TypeError) as e:
            raise ProviderJobError(
                "invalid_request", f"Tham số đọc không hợp lệ cho edge-tts: {e}",
                hint="Giọng dạng vi-VN-HoaiMyNeural; tốc độ/âm lượng dạng +0%; cao độ dạng +0Hz.",
            ) from e

        audio = bytearray()
        words: list[tuple[str, int, int]] = []
        attempt = run.attempt if run is not None else 0
        try:
            async for chunk in comm.stream():
                kind = chunk.get("type")
                if kind == "audio":
                    audio += chunk.get("data", b"")
                elif kind == BOUNDARY:
                    start = chunk["offset"] / TICKS_PER_MS
                    end = (chunk["offset"] + chunk["duration"]) / TICKS_PER_MS
                    words.append((audio_util.nfc(chunk["text"]), round(start), round(end)))
        except NoAudioReceived as e:
            raise TransientError("Dịch vụ edge-tts không trả âm thanh (lỗi tạm thời phía máy chủ).",
                                 retry_after=retry_after(attempt)) from e
        except _TRANSIENT as e:
            raise TransientError(f"Lỗi kết nối dịch vụ edge-tts ({type(e).__name__}).",
                                 retry_after=retry_after(attempt)) from e
        if not audio:
            raise TransientError("Dịch vụ edge-tts không trả âm thanh (lỗi tạm thời phía máy chủ).",
                                 retry_after=retry_after(attempt))

        duration = await anyio.to_thread.run_sync(_finish, out, bytes(audio), tts_dir, key, words)
        return TTSResult(path=out, duration_ms=duration, words=words if req.word_timings else [])
