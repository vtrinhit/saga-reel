"""Rà soát bảo mật — chèn filtergraph ffmpeg (#4): màu/cỡ title, xfade, biểu thức motion, lớp hậu kỳ, ducking; preset
kiểm chặt ở preset.create, entity.patch, file khai báo; dựng khối vẫn chặn giá trị xấu còn sót trong DB."""

from __future__ import annotations

import logging

import pytest
from sagareel.core.errors import AppError
from sagareel.render import chunks, filters, still_motion
from sagareel.render.layout import ClipSlot
from sagareel.render.profile import Profile
from sagareel.services import entities, presets
from sagareel.services.spec import plan as sp

HOSTILE_COLORS = ["white:fontfile=/etc/passwd", "red,drawtext=text=x", "x[a];[a]null", "white'", "re d"]


@pytest.mark.parametrize("color", ["white", "#FFCC00", "#FFCC0080", "yellow@0.8"])
def test_color_ok(color):
    assert filters.check_color(color) == color


@pytest.mark.parametrize("color", HOSTILE_COLORS)
def test_color_hostile(color):
    with pytest.raises(ValueError):
        filters.check_color(color)


def _spec(titles=None, xfade=None) -> chunks.ChunkSpec:
    prof = Profile(width=64, height=112, fps=30)
    c0 = ClipSlot(id=1, index=0, start_ms=0, dur_ms=1000, overlap_ms=0, transition="cut", xfade=None, key="a")
    c1 = ClipSlot(id=2, index=1, start_ms=1000, dur_ms=1000, overlap_ms=200 if xfade else 0,
                  transition="x" if xfade else "cut", xfade=xfade, key="b")
    return chunks.ChunkSpec(index=0, start_ms=0, dur_ms=2000, clips=[c0, c1], dialogue=[], sfx=[],
                            titles=titles or [], fingerprint="f" * 64,
                            options=chunks.PlanOptions(profile=prof))


@pytest.mark.parametrize("color", HOSTILE_COLORS)
def test_hostile_title_color_cannot_inject(tmp_path, color):
    t = {"id": 1, "text": "x", "start": 0, "end": 500, "params": {"color": color}}
    with pytest.raises(AppError) as e:
        chunks.build_graph(_spec([t]), dialogue_inputs=[], sfx_inputs=[], ass_path=None,
                           title_files=[tmp_path / "t.txt"])
    assert e.value.code == "render_param_invalid"


def test_hostile_title_text_stays_in_textfile(tmp_path):
    evil = "x':fontfile=/etc/passwd,drawtext=text='y;[a]"
    t = {"id": 1, "text": evil, "start": 0, "end": 500, "params": {"color": "white"}}
    g = chunks.build_graph(_spec([t]), dialogue_inputs=[], sfx_inputs=[], ass_path=None,
                           title_files=[tmp_path / "t.txt"])
    assert evil not in g and "expansion=none" in g and "textfile=" in g


def test_hostile_xfade_rejected_at_build():
    with pytest.raises(AppError):
        chunks.build_graph(_spec(xfade="fade:offset=0[x];[x]null"), dialogue_inputs=[], sfx_inputs=[],
                           ass_path=None, title_files=[])
    assert "xfade=transition=dissolve" in chunks.build_graph(_spec(xfade="dissolve"), dialogue_inputs=[],
                                                             sfx_inputs=[], ass_path=None, title_files=[])


@pytest.mark.parametrize("data", [
    {"xfade": "custom"},
    {"xfade": "fade:expr=1"},
    {"xfade": "fade", "duration_ms": "500;x"},
    {"xfade": "fade", "evil": "x"},
])
def test_transition_preset_strict(data):
    with pytest.raises(AppError) as e:
        presets.validate_data("transition", data)
    assert e.value.code == "preset_invalid"


@pytest.mark.parametrize("data", [
    {"type": "zoom", "z": "1':x=0[a];[a]null", "x": "0", "y": "0"},
    {"type": "zoom", "z": "{amt.__class__}", "x": "0", "y": "0"},
    {"type": "zoom", "z": "{secret}", "x": "0", "y": "0"},
    {"type": "post", "filter": "movie=/etc/passwd"},
    {"type": "post", "filter": "noise=alls=5[a];[a]null"},
    {"type": "post", "filter": "curves=psfile=/x.acv"},
    {"type": "zoom", "z": "1", "x": "0", "y": "0", "amt": "0.1"},
])
def test_motion_preset_strict(data):
    with pytest.raises(AppError) as e:
        presets.validate_data("motion", data)
    assert e.value.code == "preset_invalid"


def test_subtitle_style_no_control_chars():
    with pytest.raises(AppError):
        presets.validate_data("subtitle_style", {"font": "Arial\n[Events]\nDialogue: x"})
    with pytest.raises(AppError):
        presets.validate_data("subtitle_style", {"colours": {"primary": "red"}})


def test_all_builtin_presets_valid(caplog):
    with caplog.at_level(logging.ERROR, logger="sagareel.presets"):
        defs = presets.builtin_definitions()
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert {"motion", "transition", "subtitle_style"} <= {d["kind"] for d in defs}


def test_still_motion_rejects_db_row_with_bad_expr():
    prof = Profile(width=64, height=112, fps=30)
    bad = {"type": "zoom", "z": "1':x=0", "x": "0", "y": "0"}
    with pytest.raises(AppError):
        still_motion.build_filter(bad, 30, prof, factor=2, post=[], table={}, common={})
    with pytest.raises(AppError):
        still_motion.build_filter({"type": "zoom", "z": "1", "x": "0", "y": "0"}, 30, prof, factor=2, post=["p"],
                                  table={"p": {"type": "post", "filter": "movie=x.png"}}, common={})


def test_entity_patch_preset_validates(owner_ctx):
    row = presets.create(owner_ctx, "transition", "mine", "Mine", {"xfade": "fade", "duration_ms": 400})
    with pytest.raises(AppError) as e:
        entities.patch(owner_ctx, "preset", row["id"], {"data": {"xfade": "fade[x];[x]null"}})
    assert e.value.code == "preset_invalid"
    with pytest.raises(AppError):  # đổi kind sang motion → dữ liệu cũ phải hợp lệ cho motion
        entities.patch(owner_ctx, "preset", row["id"], {"kind": "motion"})


def test_spec_apply_validates_preset_and_title(owner_ctx):
    text = """
spec_version: 1
project: {key: p1, name: P}
presets: [{kind: transition, key: t1, name: T, data: {xfade: "fade[x];[x]null"}}]
"""
    with pytest.raises(AppError) as e:
        sp.plan(owner_ctx, text)
    assert e.value.code == "spec_invalid"
    text = """
spec_version: 1
project: {key: p1, name: P}
sequences: [{key: ep1, title: T, timeline: [{track: title, params: {text: hi, color: "red:fontfile=/x"}}]}]
"""
    with pytest.raises(AppError) as e:
        sp.plan(owner_ctx, text)
    assert e.value.code == "spec_invalid"


def test_timeline_title_color_validated(owner_ctx, project):
    from sagareel.services import sequences
    from sagareel.services.audio import timeline

    sid = sequences.create(owner_ctx, project, "Tập 1", key="ep1")["id"]
    with pytest.raises(AppError):
        timeline.timeline_add(owner_ctx, sid, "title", params={"text": "hi", "color": "red,drawbox"})
    ok = timeline.timeline_add(owner_ctx, sid, "title", params={"text": "hi", "color": "red"})
    with pytest.raises(AppError):
        entities.patch(owner_ctx, "timeline_item", ok["id"], {"params": {"text": "hi", "color": "x[a]"}})


def test_mix_ducking_param_validated():
    from sagareel.services.audio import mix

    with pytest.raises(ValueError):
        mix.final_audio_filter("1:a", [{"input": "2:a", "start_ms": 0, "end_ms": 1000}], [],
                               {"bed": "0:a", "total_ms": 1000, "ducking": "anullsink[x];amovie=/etc/passwd"})
