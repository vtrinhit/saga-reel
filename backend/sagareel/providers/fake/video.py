"""`fake_video`: ffmpeg zoompan từ ảnh, có tiếng sine, dài đúng `duration_s`."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from sagareel.core import durations
from sagareel.media import ffmpeg
from sagareel.providers.base import CapabilityError, ConfigField, Health, VideoAdapter, VideoRequest, VideoResult
from sagareel.providers.fake import ASPECTS, HEALTH_FIELD, aspect_size, fake_health
from sagareel.providers.registry import register_adapter


@register_adapter
class FakeVideoAdapter(VideoAdapter):
    adapter_id = "fake_video"
    label = "Video giả (không tốn tiền)"
    config_fields: ClassVar[list[ConfigField]] = [
        ConfigField("short_side", "Cạnh ngắn (px)", "int", False, 240, help="Kích thước video giả; nhỏ cho nhanh."),
        ConfigField("fps", "Khung hình/giây", "int", False, 12),
        ConfigField("durations", "Thời lượng hỗ trợ (giây)", "json", False, [],
                    help="Để trống = tạo đúng số giây được xin; nhập danh sách (vd. [5, 10]) để giả lập provider "
                         "chỉ có vài mức."),
        HEALTH_FIELD,
    ]
    default_capabilities: ClassVar[dict] = {
        "durations": [],  # rỗng = mọi số giây nguyên
        "resolutions": ["480p", "720p", "1080p"],
        "aspects": ASPECTS,
        "needs_image": True,
        "has_audio": True,
    }

    async def check_health(self) -> Health:
        return fake_health(self)

    async def estimate(self, req: VideoRequest) -> dict:
        return {"units": 1, "seconds": req.duration_s, "cost": 0}

    async def generate(self, req: VideoRequest, run=None) -> VideoResult:
        caps = self.capabilities()
        if not durations.supported(caps.get("durations"), req.duration_s):
            raise CapabilityError(f"Thời lượng {req.duration_s} s không được hỗ trợ.",
                                  hint=f"Chọn một trong {durations.sanitize(caps.get('durations'))} giây.")
        w, h = aspect_size(req.aspect, int(self.conn.options.get("short_side", 240)))
        fps = max(1, int(self.conn.options.get("fps", 12)))
        d = int(req.duration_s)
        vf = (
            f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
            f"zoompan=z='min(1+0.004*on,1.4)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={w}x{h}:fps={fps},"
            "format=yuv420p[v]"
        )
        out = Path(req.out_path)

        def on_progress(pct: float) -> None:
            if run is not None:
                run.progress(pct, stage="Đang dựng video giả")

        with ffmpeg.atomic_output(out) as part:
            await ffmpeg.run(
                [
                    "-loop", "1", "-framerate", str(fps), "-i", str(req.image),
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
                    "-filter_complex", vf, "-map", "[v]", "-map", "1:a", "-t", str(d),
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-b:a", "64k", str(part),
                ],
                timeout=300,
                total_ms=d * 1000,
                on_progress=on_progress,
                cancel=run.cancelled if run is not None else None,
            )
        return VideoResult(
            path=out,
            meta={"adapter": self.adapter_id, "prompt_received": req.prompt, "duration_s": d,
                  "resolution": req.resolution, "aspect": req.aspect, "size": [w, h]},
        )
