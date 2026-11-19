"""Layout magnetic (spec §23.1): hàm thuần `render.layout.compute`."""

from __future__ import annotations

import pytest
from sagareel.render import layout
from sagareel.render.layout import ClipIn, ItemIn, LineIn, SequenceIn


def seq(*clips, lines=(), items=()):
    return SequenceIn(clips=list(clips), lines=list(lines), items=list(items))


def test_cuts_are_contiguous_no_gaps():
    tl = layout.compute(seq(ClipIn(1, 3000), ClipIn(2, 6000), ClipIn(3, 2000)))
    assert [(c.start_ms, c.end_ms) for c in tl.clips] == [(0, 3000), (3000, 9000), (9000, 11000)]
    assert [c.overlap_ms for c in tl.clips] == [0, 0, 0]
    assert tl.total_ms == 11000
    assert all(c.transition == "cut" for c in tl.clips)


def test_empty_sequence():
    tl = layout.compute(seq())
    assert tl.clips == [] and tl.total_ms == 0


def test_fade_overlaps_and_shortens_total():
    # 3 s + 3 s với fade 0,5 s → 5,5 s (post-production.md §4)
    tl = layout.compute(seq(ClipIn(1, 3000), ClipIn(2, 3000, link="fade")))
    assert tl.clips[1].start_ms == 2500
    assert tl.clips[1].overlap_ms == 500
    assert tl.clips[1].xfade == "fade"
    assert tl.total_ms == 5500


def test_overlaps_accumulate():
    tl = layout.compute(seq(
        ClipIn(1, 4000), ClipIn(2, 4000, link="fade"), ClipIn(3, 4000, transition={"name": "fadeblack"}),
        ClipIn(4, 4000),
    ))
    # fade 500, fadeblack 1000, cut
    assert [c.start_ms for c in tl.clips] == [0, 3500, 6500, 10500]
    assert tl.total_ms == 16000 - 500 - 1000
    assert tl.clips[2].xfade == "fadeblack"


def test_new_scene_uses_longer_default():
    tl = layout.compute(seq(ClipIn(1, 4000, scene_id=1), ClipIn(2, 4000, scene_id=2, link="fade")))
    assert tl.clips[1].overlap_ms == 700


def test_explicit_duration_wins_and_is_frame_aligned():
    tl = layout.compute(seq(ClipIn(1, 4000), ClipIn(2, 4000, transition={"name": "dissolve", "duration_ms": 610})))
    # 610 ms → 18 khung (600 ms) ở 30 fps
    assert tl.clips[1].overlap_ms == 600
    assert tl.total_ms == 7400


def test_transition_clamped_to_half_shorter_clip():
    tl = layout.compute(seq(ClipIn(1, 1000), ClipIn(2, 5000, transition={"name": "fade", "duration_ms": 2000})))
    assert tl.clips[1].overlap_ms == 500
    assert any(w["code"] == "transition_clamped" for w in tl.warnings)


def test_continue_and_unknown_transition_are_cuts():
    tl = layout.compute(seq(ClipIn(1, 2000), ClipIn(2, 2000, link="continue"),
                            ClipIn(3, 2000, transition={"name": "wipe_star"})))
    assert [c.overlap_ms for c in tl.clips] == [0, 0, 0]
    assert any(w["code"] == "transition_unknown" for w in tl.warnings)


def test_explicit_cut_overrides_fade_link():
    tl = layout.compute(seq(ClipIn(1, 2000), ClipIn(2, 2000, link="fade", transition={"name": "cut"})))
    assert tl.clips[1].overlap_ms == 0


def test_dialogue_follows_clip():
    lines = [LineIn(10, clip_id=2, offset_ms=300, duration_ms=1500, text="Xin chào")]
    a = layout.compute(seq(ClipIn(1, 3000), ClipIn(2, 3000), lines=lines))
    assert (a.dialogue[0].start_ms, a.dialogue[0].end_ms) == (3300, 4800)
    # clip 1 dài ra 6 → 10 s: câu thoại của clip 2 đi theo
    b = layout.compute(seq(ClipIn(1, 10000), ClipIn(2, 3000), lines=lines))
    assert b.dialogue[0].start_ms == 10300


def test_dialogue_orphan_and_overflow_warn():
    tl = layout.compute(seq(ClipIn(1, 1000), lines=[
        LineIn(1, clip_id=99, duration_ms=500), LineIn(2, clip_id=1, offset_ms=500, duration_ms=2000)]))
    codes = {w["code"] for w in tl.warnings}
    assert {"dialogue_orphan", "dialogue_overflow"} <= codes
    assert tl.dialogue[0].end_ms == 1000


def test_items_anchor_to_clips_and_move_with_reorder():
    items = [ItemIn(5, "sfx", anchor_clip_id=2, offset_ms=250, media_duration_ms=400)]
    a = layout.compute(seq(ClipIn(1, 2000), ClipIn(2, 3000), items=items))
    assert (a.sfx[0].start_ms, a.sfx[0].end_ms) == (2250, 2650)
    b = layout.compute(seq(ClipIn(2, 3000), ClipIn(1, 2000), items=items))
    assert b.sfx[0].start_ms == 250


def test_item_end_anchor_clip_and_sequence_end():
    items = [
        ItemIn(1, "music", anchor_clip_id=1, end_anchor={"clip_id": 2}, media_duration_ms=60000),
        ItemIn(2, "music", anchor_clip_id=2, end_anchor={"sequence_end": True}, media_duration_ms=1000),
        ItemIn(3, "title", anchor_clip_id=1, offset_ms=100, duration_ms=1500, params={"text": "Tập 1"}),
    ]
    tl = layout.compute(seq(ClipIn(1, 2000), ClipIn(2, 2000), ClipIn(3, 2000), items=items))
    assert (tl.music[0].start_ms, tl.music[0].end_ms) == (0, 4000)
    assert (tl.music[1].start_ms, tl.music[1].end_ms) == (2000, 6000)
    assert (tl.titles[0].start_ms, tl.titles[0].end_ms, tl.titles[0].text) == (100, 1600, "Tập 1")


def test_music_loop_runs_to_end_and_is_clamped():
    items = [ItemIn(1, "music", media_duration_ms=500, params={"loop": True}),
             ItemIn(2, "sfx", duration_ms=99999)]
    tl = layout.compute(seq(ClipIn(1, 3000), items=items))
    assert tl.music[0].end_ms == 3000
    assert tl.sfx[0].end_ms == 3000


def test_missing_anchor_falls_back_to_start():
    tl = layout.compute(seq(ClipIn(1, 3000), items=[ItemIn(1, "sfx", anchor_clip_id=42, offset_ms=100,
                                                           media_duration_ms=200)]))
    assert tl.sfx[0].start_ms == 100
    assert any(w["code"] == "anchor_missing" for w in tl.warnings)


def test_item_outside_sequence_dropped():
    tl = layout.compute(seq(ClipIn(1, 1000), items=[ItemIn(1, "sfx", offset_ms=5000, media_duration_ms=200)]))
    assert tl.sfx == []
    assert any(w["code"] == "item_empty" for w in tl.warnings)


@pytest.mark.parametrize("n", [1, 7, 50])
def test_total_equals_sum_minus_overlaps(n):
    clips = [ClipIn(i, 2000 + 100 * i, link="fade" if i % 3 == 0 else "cut") for i in range(n)]
    tl = layout.compute(seq(*clips))
    assert tl.total_ms == sum(c.dur_ms for c in tl.clips) - sum(c.overlap_ms for c in tl.clips)
    for a, b in zip(tl.clips, tl.clips[1:], strict=False):
        assert b.start_ms == a.end_ms - b.overlap_ms


def test_custom_transition_table():
    table = {"fade": {"xfade": "fade", "duration_ms": 300}}
    tl = layout.compute(seq(ClipIn(1, 2000), ClipIn(2, 2000, link="fade")), transitions=table)
    assert tl.clips[1].overlap_ms == 300


def test_builtin_transition_presets_match_research():
    from sagareel.render import presets

    t = presets.load("transition")
    assert set(t) >= {"cut", "fade", "fadeblack", "dissolve", "fadewhite"}
    assert t["fade"]["duration_ms"] == 500 and t["fade"]["new_scene_duration_ms"] == 700
    assert t["fadeblack"]["duration_ms"] == 1000
    tl = layout.compute(seq(ClipIn(1, 4000), ClipIn(2, 4000, link="fade")), transitions=t)
    assert tl.clips[1].overlap_ms == 500
