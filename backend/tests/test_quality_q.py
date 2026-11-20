"""Sửa chất lượng từ kiểm chứng thật (task q): metadata theo ngôn ngữ gói, bìa từ ảnh sạch + chữ ở dải ít chi tiết."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PIL import Image, ImageChops, ImageDraw
from sagareel.services import llm
from sagareel.services.export import checks, covers, metadata, platforms

PROJECT = {"id": 1, "key": "sen-ho", "name": "Sen Hồ", "description": "Phim về bến sông", "genre_key": "serial",
           "language": "vi"}


def _seq(**kw):
    base = {"id": 1, "key": "ep001", "number": 1, "title": "Bến sương", "synopsis": "Cô gái đợi ba ở bến sông.",
            "script": "Năm ấy…", "settings": {}, "dialogue": []}
    base.update(kw)
    return base


@pytest.fixture
def spec(services):
    return platforms.get(services, "tiktok")


# --- 3. Metadata theo ngôn ngữ gói ----------------------------------------------------------------------------------


async def test_llm_caption_gets_target_lang_and_translates(services, spec, monkeypatch):
    seen: list[dict] = []

    async def fake_run(_services, task_key, variables, **_kw):
        seen.append({"task": task_key, **variables})
        return SimpleNamespace(data={"title": "Misty Pier", "caption": "A girl waits for her father.",
                                     "hashtags": ["misty pier", "drama"], "keywords": ["misty pier", "Sen Ho"],
                                     "pinned_comment": "Would you have waited?", "cover_title": "Misty Pier"})

    monkeypatch.setattr(llm, "run_task", fake_run)
    md = await metadata.build(services, spec=spec, project=PROJECT, sequence=_seq(), lang="en", chapters=[], music=[])
    v = seen[0]
    assert v["task"] == "caption" and v["lang"] == "en"
    assert "Target language: en" in v["input"] and "translate" in v["input"].lower()
    assert md["title"] == "Misty Pier" and md["keywords"] == ["misty pier", "Sen Ho"]
    assert md["pinned_comment"] == "Would you have waited?" and md["cover_title"] == "Misty Pier"
    assert not md.get("needs_translation")


async def test_template_uses_sequence_translation(services, spec):
    seq = _seq(settings={"translations": {"en": {"title": "Misty Pier", "synopsis": "A girl waits by the river."}}})
    md = await metadata.build(services, spec=spec, project=PROJECT, sequence=seq, lang="en", chapters=[], music=[],
                              use_llm=False)
    assert md["title"] == "Misty Pier" and md["description"] == "A girl waits by the river."
    assert "Bến sương" not in md["keywords"] and not md.get("needs_translation")
    assert md["pinned_comment"].startswith("What did you think") and md["cover_title"] == "Misty Pier"
    assert md["episode_label"] == "Episode 1"


async def test_template_without_translation_marks_needs_translation(services, spec):
    seq = _seq(dialogue=[{"text": "Ba ơi, con đợi ba ở đây nè.", "translations": {"en": "Dad, I'm waiting here."}}])
    md = await metadata.build(services, spec=spec, project=PROJECT, sequence=seq, lang="en", chapters=[], music=[],
                              use_llm=False)
    assert md["needs_translation"] == ["title"]
    assert md["description"] == "Dad, I'm waiting here."  # từ bản dịch lời thoại
    assert "Bến sương" not in md["keywords"] and md["cover_title"] is None  # không vẽ chữ Việt lên bìa tiếng Anh
    report = checks.run_checks(spec, duration_s=30, measure_loudness=False, metadata=md)
    assert "needs_translation" in [w["code"] for w in report["warnings"]]
    assert "needs_translation" not in [e["code"] for e in report["errors"]]


async def test_same_language_unchanged(services, spec):
    md = await metadata.build(services, spec=spec, project=PROJECT, sequence=_seq(), lang="vi", chapters=[], music=[],
                              use_llm=False)
    assert md["title"] == "Bến sương" and md["cover_title"] == "Bến sương" and not md.get("needs_translation")
    assert md["episode_label"] == "Tập 1"


def test_part_label_localized(spec):
    md = {"title": "Misty Pier (Part 1/2)", "episode_label": "Episode 1", "description": "x", "lang": "en"}
    out = metadata.part_variant(spec, md, 2, 2, [])
    assert out["title"] == "Misty Pier (Part 2/2)" and out["episode_label"] == "Episode 1 / Part 2"


# --- 1. Bìa ---------------------------------------------------------------------------------------------------------


def _busy_top(w: int = 1080, h: int = 1920) -> Image.Image:
    """Nền phẳng, một khối nhiều chi tiết (như khuôn mặt) nằm ở dải trên 10–24%."""
    img = Image.new("RGB", (w, h), (120, 110, 100))
    d = ImageDraw.Draw(img)
    for y in range(round(h * 0.10), round(h * 0.25), 6):
        for x in range(0, w, 6):
            d.rectangle([x, y, x + 2, y + 2], fill=(255, 255, 255) if (x + y) % 12 else (0, 0, 0))
    return img


def test_title_goes_to_band_with_least_detail():
    img = _busy_top()
    zone = {"top": 160, "bottom": 480, "left": 60, "right": 140}
    band = covers.pick_band(img, zone)
    assert band[0] >= round(1920 * 0.5)  # dải dưới-giữa
    out = covers.draw_title(img.copy(), "Bến sương", zone)
    box = ImageChops.difference(out, img).getbbox()
    assert box is not None and box[1] >= round(1920 * 0.45) and box[3] <= 1920 - zone["bottom"] + 2
    # dải trên (khuôn mặt) không bị đụng tới
    top = (0, round(1920 * 0.10), 1080, round(1920 * 0.25))
    assert ImageChops.difference(out.crop(top), img.crop(top)).getbbox() is None


def test_title_band_ties_go_top():
    img = Image.new("RGB", (1080, 1920), (40, 90, 160))
    zone = {"top": 160, "bottom": 480, "left": 60, "right": 140}
    assert covers.pick_band(img, zone)[0] < 1920 * 0.3


async def test_covers_prefer_image_take_over_subtitled_video(tmp_path, spec, monkeypatch):
    stills = []
    for i, color in enumerate(((30, 30, 30), (200, 180, 150))):
        p = tmp_path / f"still{i}.png"
        img = _busy_top() if i else Image.new("RGB", (1080, 1920), color)
        img.save(p)
        stills.append({"clip_id": 10 + i, "path": p, "kind": "image", "has_character": bool(i),
                       "start_ms": 1000 * i, "end_ms": 1000 * (i + 1)})

    async def boom(*_a, **_kw):  # khung từ video đã burn phụ đề: không được dùng khi có ảnh take
        raise AssertionError("không được lấy khung từ video thành phẩm")

    monkeypatch.setattr(covers, "candidate_times", boom)
    monkeypatch.setattr(covers, "_extract", boom)
    out = await covers.make_covers(tmp_path / "final.mp4", tmp_path / "out", spec, duration_ms=2000,
                                   title="Bến sương", n=1, stills=stills)
    assert len(out) == 1 and out[0]["source"] == "image_take" and out[0]["clip_id"] == 11
    assert 1000 <= out[0]["timestamp_ms"] < 2000
    assert (tmp_path / "out" / "cover-1.jpg").is_file()


async def test_covers_fall_back_to_video_without_stills(tmp_path, spec, monkeypatch):
    calls = []

    async def fake_times(video, duration_ms, n=3, marks_ms=()):
        return [500]

    async def fake_extract(video, t_s, out, scale=None):
        calls.append(t_s)
        Image.new("RGB", (1080, 1920), (90, 90, 90)).save(out)

    monkeypatch.setattr(covers, "candidate_times", fake_times)
    monkeypatch.setattr(covers, "_extract", fake_extract)
    out = await covers.make_covers(tmp_path / "final.mp4", tmp_path / "out", spec, duration_ms=2000, title=None, n=1)
    assert out[0]["source"] == "final_video" and calls == [0.5]


# --- 1. Bìa trong gói xuất thật (pipeline giả của test_export) ------------------------------------------------------

from test_export import export, fakes, fast, outdir, seq_id  # noqa: F401 — dùng lại fixture/hàm của Task F


def test_package_cover_uses_image_take(client, settings, owner_ctx, project, seq_id, fakes, tmp_path):  # noqa: F811
    import json

    from sagareel.db.models import Asset, Clip, Take
    from sagareel.services import media
    from sqlalchemy import select

    src = tmp_path / "take.png"
    _busy_top().save(src)
    with owner_ctx.uow(op_name="test.take") as s:
        mid = media.register(s, src, kind="image", origin="upload", settings=settings, project_id=project).id
        hero = Asset(project_id=project, key="linh", kind="character", name="Linh", description="cô gái")
        s.add(hero)
        s.flush()
        clip = s.scalars(select(Clip).where(Clip.sequence_id == seq_id).order_by(Clip.rank)).all()[1]
        clip.assets = [{"asset_id": hero.id}]
        t = Take(owner_type="clip", owner_id=clip.id, part="image", media_id=mid, status="succeeded")
        s.add(t)
        s.flush()
        clip.image_take_id = t.id
        clip_id = clip.id
    fast(client, "tiktok")
    out = export(client, seq_id, ["tiktok"], options={"covers": 1})
    assert out["job"]["status"] == "succeeded", out["job"]
    md = json.loads((outdir(settings, "exports/demo-1/ep1/tiktok-vi") / "metadata.json").read_text(encoding="utf-8"))
    assert md["covers"][0]["source"] == "image_take" and md["covers"][0]["clip_id"] == clip_id
    assert md["cover_timestamp_ms"] == md["covers"][0]["timestamp_ms"] is not None
