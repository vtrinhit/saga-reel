"""still_motion (ảnh → chuyển động) và preset motion dữ liệu."""

from __future__ import annotations

import pytest
from _fixtures_e import frame_count, make_image, stream_durations, streams
from sagareel.core.errors import AppError
from sagareel.render import presets, still_motion
from sagareel.render.profile import Profile

PROF = Profile(64, 114)


def test_builtin_motion_presets_exist():
    table = presets.load("motion")
    for key in ("slow_zoom_in", "slow_zoom_out", "pan_left", "pan_right", "pan_up", "pan_down",
                "ken_burns_diag", "handheld", "grain", "vignette", "light_flicker"):
        assert key in table, key
    assert table["grain"]["filter"] == "noise=alls=5:allf=t"
    assert table["vignette"]["filter"] == "vignette=PI/6:eval=init"
    common = presets.common("motion")
    assert common["upscale_factor"] == 4 and common["max_zoom_per_s"] == 0.02


def test_filter_matches_research_zoom_in():
    table = presets.load("motion")
    data = still_motion.resolve_preset("slow_zoom_in", table)
    vf = still_motion.build_filter(data, 180, Profile(1080, 1920), factor=4, post=None, table=table,
                                   common=presets.common("motion"))
    # 6 s: mức phóng 0,08 (≤ 0,02/s × 6), smoothstep, phóng 4× lanczos trước zoompan
    assert vf.startswith("scale=4320:7680:force_original_aspect_ratio=increase:flags=lanczos")
    assert "zoompan=z='1+0.08000*(3*pow(on/180,2)-2*pow(on/180,3))'" in vf
    assert ":d=180:s=1080x1920:fps=30" in vf
    assert "noise=alls=5:allf=t,vignette=PI/6:eval=init" in vf


def test_zoom_and_pan_speed_limited_by_duration():
    table = presets.load("motion")
    common = presets.common("motion")
    short = still_motion.motion_params(still_motion.resolve_preset("slow_zoom_in", table), 30, 30, common)
    assert float(short["amt"]) == pytest.approx(0.02)  # 1 s → tối đa 2 %
    pan = still_motion.motion_params(still_motion.resolve_preset("pan_right", table), 30, 30, common)
    # lia ≤ 4 % chiều rộng/giây: span × (1 − 1/1.15) ≈ 0.04
    assert float(pan["span"]) * (1 - 1 / 1.15) == pytest.approx(0.04, abs=1e-3)
    assert float(pan["start"]) == pytest.approx((1 - float(pan["span"])) / 2, abs=1e-4)


def test_post_override_and_unknown_preset():
    table = presets.load("motion")
    data = still_motion.resolve_preset("handheld", table)
    vf = still_motion.build_filter(data, 30, PROF, factor=4, post=["light_flicker"], table=table,
                                   common=presets.common("motion"))
    assert "eq=brightness=" in vf and "noise=" not in vf
    with pytest.raises(AppError) as e:
        still_motion.resolve_preset("nope", table)
    assert e.value.code == "preset_not_found"
    with pytest.raises(AppError):
        still_motion.resolve_preset("grain", table)  # lớp hậu kỳ, không phải chuyển động


def test_preset_table_overrides_from_db(owner_ctx, project):
    from sagareel.db.models import Preset

    with owner_ctx.services.db.session() as s:
        s.add(Preset(kind="motion", key="slow_zoom_in", name="x", data={"amt": 0.05}, project_id=project))
        s.add(Preset(kind="motion", key="my_push", name="Đẩy", data={"z": "1+{amt}*{ease}", "x": "0", "y": "0",
                                                                    "amt": 0.04}))
        s.commit()
        table = presets.load("motion", s, project)
        other = presets.load("motion", s, None)
    assert table["slow_zoom_in"]["amt"] == 0.05 and table["slow_zoom_in"]["post"] == ["grain", "vignette"]
    assert other["slow_zoom_in"]["amt"] == 0.08
    assert "my_push" in table


@pytest.mark.parametrize("preset", ["slow_zoom_in", "pan_left", "ken_burns_diag", "handheld", ""])
async def test_make_segment_exact_length(tmp_path, preset):
    img = make_image(tmp_path / "a.png", color="test")
    out = await still_motion.make_segment(img, preset, 1000, PROF, cache_dir=tmp_path / "cache")
    assert out.is_file() and out.suffix == ".mov"
    s = streams(out)
    v = next(x for x in s if x["codec_type"] == "video")
    a = next(x for x in s if x["codec_type"] == "audio")
    assert (v["width"], v["height"], v["pix_fmt"], v["r_frame_rate"]) == (64, 114, "yuv420p", "30/1")
    assert (a["sample_rate"], a["channels"]) == ("48000", 2)
    assert frame_count(out) == 30
    vd, ad = stream_durations(out)
    assert abs(vd - ad) < 1 / 30


async def test_make_segment_accepts_dict_profile_and_caches(tmp_path):
    img = make_image(tmp_path / "a.png")
    cache_dir = tmp_path / "cache"
    a = await still_motion.make_segment(img, "slow_zoom_out", 500, {"width": 64, "height": 114}, cache_dir=cache_dir)
    mtime = a.stat().st_mtime_ns
    b = await still_motion.make_segment(img, "slow_zoom_out", 500, {"width": 64, "height": 114}, cache_dir=cache_dir)
    assert a == b and b.stat().st_size > 0
    assert frame_count(b) == 15
    assert mtime <= b.stat().st_mtime_ns
    c = await still_motion.make_segment(img, "pan_right", 500, {"width": 64, "height": 114}, cache_dir=cache_dir)
    assert c != a
    assert len(list((cache_dir / "segments").rglob("*.mov"))) == 2


async def test_make_segment_missing_image(tmp_path):
    with pytest.raises(AppError) as e:
        await still_motion.make_segment(tmp_path / "x.png", "slow_zoom_in", 1000, PROF, cache_dir=tmp_path)
    assert e.value.code == "render_bad_media"
