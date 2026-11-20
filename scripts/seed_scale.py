"""Dựng dữ liệu quy mô lớn (spec §15/§19/§20: 1000 clip, 200 tập, 50 job) để đo hiệu năng backend.

Chạy: `python scripts/seed_scale.py --data-dir D:\\tmp\\scale [--small]`
(đặt `PYTHONPATH=backend`). Chỉ dùng nhà cung cấp GIẢ: không gọi API thật, media là một file tí hon dùng chung.

Dùng op/service trong tiến trình cho thực thể có lịch sử (project, tập, clip, bible, sửa clip); dựng thẳng dòng DB cho
phần nhiều nhưng không cần lịch sử (lời thoại, take, job) — cùng cách các fixture test làm.
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

BIG_CLIPS = 1000
EPISODES = 200
SMALL_CLIPS = 20
BIBLE_ITEMS = 30
JOBS = 50
LINES_PER_CLIP = 2
TAKES_PER_CLIP = 3


def _tiny_media_file(dst: Path) -> tuple[Path, str]:
    """Video 64x112 1 s bằng ffmpeg; không có ffmpeg thì rơi về ảnh PNG (kind=image)."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        out = dst / "tiny.mp4"
        r = subprocess.run(
            [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
             "testsrc2=s=64x112:d=1:r=30", "-f", "lavfi", "-i", "sine=d=1", "-shortest", "-c:v", "libx264",
             "-pix_fmt", "yuv420p", "-c:a", "aac", str(out)],
            check=False, stdin=subprocess.DEVNULL, capture_output=True,
        )
        if r.returncode == 0 and out.exists():
            return out, "video"
    from PIL import Image

    out = dst / "tiny.png"
    Image.new("RGB", (64, 112), (30, 60, 90)).save(out)
    return out, "image"


class Runner:
    """Chạy op async trên một event loop dùng lại (script chạy đồng bộ)."""

    def __init__(self, ctx) -> None:
        from sagareel.core import ops

        self.ctx = ctx
        self.ops = ops
        self.loop = asyncio.new_event_loop()

    def __call__(self, name: str, **payload: Any) -> Any:
        return self.loop.run_until_complete(self.ops.invoke(name, payload, self.ctx))

    def close(self) -> None:
        self.loop.close()


def seed(data_dir: Path, *, small: bool = False, log=print) -> dict[str, Any]:
    """Dựng dữ liệu trong `data_dir`; trả id/đếm cần cho bài đo. `small=True`: bản thu nhỏ để thử script nhanh."""
    from sagareel import handlers
    from sagareel import ops as ops_pkg
    from sagareel.config import load_settings
    from sagareel.container import Services
    from sagareel.core import rank
    from sagareel.core.context import OpContext
    from sagareel.db.models import Clip, DialogueLine, Job, Take
    from sagareel.db.session import Database
    from sagareel.services import media, projects
    from sqlalchemy import select

    big_n, episodes = (50, 6) if small else (BIG_CLIPS, EPISODES)
    settings = load_settings(data_dir=data_dir, env_file=data_dir / ".env.local", outputs_dir=data_dir / "outputs")
    db = Database(settings.db_path)
    db.migrate()
    handlers.load_all()
    ops_pkg.load_all()
    svc = Services(settings=settings, boot_id="s" * 32, db=db)
    ctx = OpContext(services=svc, actor="user:owner", role="owner")
    call = Runner(ctx)
    info: dict[str, Any] = {"data_dir": str(data_dir), "db": db, "services": svc, "ctx": ctx, "call": call}

    log("project + tập …")
    project_id = projects.create(ctx, name="Scale", key="scale")["id"]
    ctx = OpContext(services=svc, actor="user:owner", role="owner", project_id=project_id)
    call.ctx = ctx
    info["ctx"] = ctx
    seq_ids = [call("sequence.create", project_id=project_id, title=f"Tập {i + 1}")["id"] for i in range(episodes)]
    big_seq = seq_ids[0]
    info.update(project_id=project_id, sequence_ids=seq_ids, big_sequence_id=big_seq)

    log("clip …")
    big_clip_ids: list[int] = []
    step = 500
    for off in range(0, big_n, step):
        n = min(step, big_n - off)
        rows = [{"title": f"Clip {off + i + 1}", "image_prompt": f"Cảnh {off + i + 1}: nhân vật bước vào phòng",
                 "motion_prompt": "máy quay đẩy chậm", "duration_ms": 3000 + (i % 5) * 500,
                 "shot_size": ("ws", "mcu", "cu")[i % 3]} for i in range(n)]
        big_clip_ids += [c["id"] for c in call("clip.bulk_create", sequence_id=big_seq, clips=rows)["items"]]
    small_clip_ids: list[int] = []
    for sid in seq_ids[1:]:
        rows = [{"title": f"Clip {i + 1}", "image_prompt": "Cảnh ngắn", "duration_ms": 4000}
                for i in range(SMALL_CLIPS)]
        small_clip_ids += [c["id"] for c in call("clip.bulk_create", sequence_id=sid, clips=rows)["items"]]
    info.update(big_clip_ids=big_clip_ids, small_clip_ids=small_clip_ids)

    log("media dùng chung, lời thoại, take …")
    tmp = Path(tempfile.mkdtemp(prefix="seed_scale_"))
    try:
        src, kind = _tiny_media_file(tmp)
        with db.session() as s:
            m = media.register(s, src, kind=kind, origin="fake", settings=settings, project_id=project_id)
            media_id = m.id
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    info["media_id"] = media_id

    with db.session() as s:
        all_clips = [(cid, sid) for cid, sid in s.execute(select(Clip.id, Clip.sequence_id)).all()]
        line_keys = rank.n_after(None, LINES_PER_CLIP)
        s.add_all([
            DialogueLine(clip_id=cid, rank=line_keys[j], text=f"Lời thoại {j + 1} của clip {cid}",
                         source="tts", offset_ms=j * 1200)
            for cid, _ in all_clips for j in range(LINES_PER_CLIP)
        ])
        s.flush()
        big_set = set(big_clip_ids)
        clip_rows = {c.id: c for c in s.scalars(select(Clip).where(Clip.id.in_(big_set))).all()}
        for i, cid in enumerate(big_clip_ids):
            takes = [Take(owner_type="clip", owner_id=cid, part=("image", "image", "video")[j], media_id=media_id,
                          status="succeeded", cost=0.01, seed=i * 10 + j) for j in range(TAKES_PER_CLIP)]
            s.add_all(takes)
            s.flush()
            c = clip_rows[cid]
            c.image_take_id, c.video_take_id = takes[1].id, takes[2].id
            c.review = ("pending", "approved", "rejected", "pending")[i % 4]
            if i % 7 == 0:
                c.flags = {"stale_video": True}
    log("bible …")
    types = ["character", "location", "event", "fact", "storyline"]
    for i in range(BIBLE_ITEMS):
        call("bible.create", project_id=project_id, type=types[i % len(types)],
             summary=f"Mục bible số {i}: Thám tử Lâm điều tra vụ án ở cảng Hải Phòng",
             evidence=[{"quote": "Lâm bước vào kho cảng lúc nửa đêm", "sequence_id": big_seq}])

    log("job …")
    statuses = (["succeeded"] * 20 + ["failed"] * 5 + ["running"] * 6 + ["queued"] * 12 + ["cancelled"] * 3
                + ["waiting_review"] * 2 + ["paused"] * 2)
    with db.session() as s:
        for i in range(JOBS):
            cid = big_clip_ids[i % len(big_clip_ids)]
            s.add(Job(type="provider.image", status=statuses[i % len(statuses)], lane="fake-image", project_id=project_id,
                      sequence_id=big_seq, clip_id=cid, params={"prompt": "giả", "i": i},
                      progress=1.0 if statuses[i % len(statuses)] == "succeeded" else 0.0,
                      result={"media_id": media_id}))

    log("lịch sử (sửa clip) …")
    for i in range(0 if small else 150):
        call("clip.update", id=big_clip_ids[i * 5], values={"notes": f"ghi chú {i}"})
    call("clip.bulk_update", ids=big_clip_ids[:100], values={"notes": "đợt sửa hàng loạt"})
    return info


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--small", action="store_true", help="bản thu nhỏ (50 clip, 6 tập)")
    a = ap.parse_args()
    a.data_dir.mkdir(parents=True, exist_ok=True)
    info = seed(a.data_dir, small=a.small)
    print({k: v for k, v in info.items() if k in ("project_id", "big_sequence_id", "data_dir")})
    info["db"].dispose()


if __name__ == "__main__":
    sys.exit(main())
