"""`fake_image`: Pillow vẽ ảnh đúng tỉ lệ, ghi prompt nguyên văn vào `meta["prompt_received"]` và `<ảnh>.prompt.txt`."""

from __future__ import annotations

import hashlib
import textwrap
import unicodedata
from pathlib import Path
from typing import ClassVar

import anyio
from PIL import Image, ImageDraw, ImageFont

from sagareel.providers.base import CapabilityError, ConfigField, Health, ImageAdapter, ImageRequest, ImageResult
from sagareel.providers.fake import ASPECTS, HEALTH_FIELD, aspect_size, fake_health
from sagareel.providers.registry import register_adapter


def _ascii(text: str) -> str:
    """Chữ không dấu để vẽ (font mặc định có thể thiếu glyph tiếng Việt); prompt thật vẫn lưu nguyên văn."""
    folded = unicodedata.normalize("NFKD", text.replace("đ", "d").replace("Đ", "D"))
    return folded.encode("ascii", "ignore").decode("ascii")


def _draw(path: Path, size: tuple[int, int], prompt: str, index: int) -> None:
    digest = hashlib.sha256(f"{prompt}\0{index}".encode()).digest()
    bg = (40 + digest[0] % 160, 40 + digest[1] % 160, 40 + digest[2] % 160)
    im = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(im)
    w, h = size
    d.rectangle([4, 4, w - 5, h - 5], outline=(255, 255, 255), width=3)
    d.line([(0, 0), (w, h)], fill=(255, 255, 255), width=1)
    d.line([(0, h), (w, 0)], fill=(255, 255, 255), width=1)
    try:
        font = ImageFont.load_default(size=max(10, w // 18))
        text = "\n".join(textwrap.wrap(_ascii(prompt), width=24)[:8]) or "(rong)"
        d.multiline_text((12, 12), f"FAKE #{index + 1}\n{text}", fill=(255, 255, 255), font=font)
    except Exception:  # noqa: BLE001 — chữ chỉ để trang trí; không được làm hỏng job
        pass
    im.save(path, "PNG")


@register_adapter
class FakeImageAdapter(ImageAdapter):
    adapter_id = "fake_image"
    label = "Ảnh giả (không tốn tiền)"
    config_fields: ClassVar[list[ConfigField]] = [
        ConfigField("short_side", "Cạnh ngắn (px)", "int", False, 360, help="Kích thước ảnh giả; nhỏ cho nhanh."),
        ConfigField("count", "Số ảnh mỗi lần", "int", False, 1),
        HEALTH_FIELD,
    ]
    default_capabilities: ClassVar[dict] = {
        "max_refs": 9,
        "aspects": ASPECTS,
        "resolutions": ["1080p", "1440p", "2160p"],
        "models": ["fake-image"],
    }

    async def check_health(self) -> Health:
        return fake_health(self)

    async def estimate(self, req: ImageRequest) -> dict:
        return {"units": int(self.conn.options.get("count", 1)), "cost": 0}

    async def generate(self, req: ImageRequest, run=None) -> ImageResult:
        caps = self.capabilities()
        if len(req.refs) > caps["max_refs"]:
            raise CapabilityError(f"Adapter giả nhận tối đa {caps['max_refs']} ảnh tham chiếu.")
        if req.aspect not in caps["aspects"]:
            raise CapabilityError(f"Tỉ lệ {req.aspect} không được hỗ trợ.", hint=f"Chọn một trong {caps['aspects']}.")
        out_dir = Path(req.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        size = aspect_size(req.aspect, int(self.conn.options.get("short_side", 360)))
        count = max(1, int(self.conn.options.get("count", 1)))
        paths = [out_dir / f"image_{i}.png" for i in range(count)]

        def work() -> None:
            for i, p in enumerate(paths):
                _draw(p, size, req.prompt, i)
                # write_bytes: không qua text mode (Windows đổi \n → \r\n) và luôn UTF-8 → đúng nguyên văn.
                p.with_suffix(".prompt.txt").write_bytes(req.prompt.encode("utf-8"))

        await anyio.to_thread.run_sync(work)
        if run is not None:
            run.progress(100.0, stage="Đã vẽ ảnh giả")
        return ImageResult(
            paths=paths,
            meta={
                "adapter": self.adapter_id,
                "prompt_received": req.prompt,
                "prompt_file": str(paths[0].with_suffix(".prompt.txt")),
                "aspect": req.aspect,
                "resolution": req.resolution,
                "model": req.model or self.conn.default_model or "fake-image",
                "refs": len(req.refs),
                "size": list(size),
            },
        )
