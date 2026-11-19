"""Task F: preset (nạp cài sẵn, kiểm tra, CRUD), preset nền tảng/thể loại, phép tính thuần của gói xuất."""

from __future__ import annotations

import copy
import itertools

import pytest
from sagareel.core.errors import AppError, Forbidden, ValidationFailed
from sagareel.db.models import Preset
from sagareel.services import presets
from sagareel.services.export import checks, metadata, platforms

PLATFORMS = ["fb-reels", "fb-video", "yt-shorts", "yt-long", "tiktok"]
GENRES = ["serial", "series", "narration", "montage", "storyboard"]


def call(client, name: str, payload: dict | None = None):
    return client.post(f"/api/ops/{name}", json=payload or {})


def spec_of(key: str) -> dict:
    d = presets.builtin_definition("platform", key)
    assert d is not None
    return d["data"]


# --- Preset cài sẵn -------------------------------------------------------------------------------------------------


def test_builtin_files_all_valid_and_complete():
    defs = presets.builtin_definitions()
    assert {d["key"] for d in defs if d["kind"] == "platform"} == set(PLATFORMS)
    assert {d["key"] for d in defs if d["kind"] == "genre"} == set(GENRES)
    for d in defs:
        assert d["name"]
        if d["kind"] == "platform":
            assert d["data"]["checked_at"] == "2026-11-19" and d["data"]["sources"]


def test_platform_numbers_match_research():
    r, fv, ys, yl, tt = (spec_of(k) for k in PLATFORMS)
    assert (r["width"], r["height"], r["duration_s"]["max"]) == (1080, 1920, 90)
    assert r["safe_zone_px"] == {"top": 269, "bottom": 672, "left": 65, "right": 65}
    assert (fv["width"], fv["height"], fv["aspect"]) == (1080, 1350, "4:5")
    assert ys["duration_s"]["max"] == 180 and ys["title"]["max_chars"] == 100 and ys["caption"]["max_bytes"] == 5000
    assert (yl["width"], yl["height"]) == (1920, 1080) and yl["chapters"]["supported"] and yl["encode"]["gop_s"] == 0.5
    assert tt["cover"]["supported"] is False and tt["caption"]["max_utf16"] == 2200
    assert all(s["ai_label"]["required"] and s["loudness"]["target_lufs"] == -14 for s in (r, fv, ys, yl, tt))


def test_genre_numbers_match_research():
    g = {d["key"]: d["data"] for d in presets.builtin_definitions() if d["kind"] == "genre"}
    assert g["serial"]["episode_s"] == [60, 90] and g["serial"]["clips"] == [12, 18] and g["serial"]["end"] == "cliffhanger"
    assert g["narration"]["transition"] == "crossfade" and g["narration"]["ken_burns"]["pan_max_pct"] == 6
    assert g["montage"]["beat_sync"] is True and g["montage"]["end"] == "loop"
    assert g["series"]["duck_db"] == 11 and g["storyboard"]["episode_s"] == [30, 60]


# --- Kiểm tra theo loại ---------------------------------------------------------------------------------------------


def test_platform_validation_is_strict():
    good = spec_of("tiktok")
    assert presets.validate_data("platform", good)["width"] == 1080
    for mutate in (
        lambda d: d.update(width=1000),                       # lệch aspect 9:16
        lambda d: d.update(typo_field=1),                     # khoá lạ
        lambda d: d["duration_s"].update(max=0.5),            # recommended ngoài [min, max]
        lambda d: d["encode"].update(crf=99),
        lambda d: d.pop("loudness"),
        lambda d: d["cover"].update(method="magic"),
        lambda d: d.update(sources=[]),
    ):
        bad = copy.deepcopy(good)
        mutate(bad)
        with pytest.raises(ValidationFailed) as e:
            presets.validate_data("platform", bad)
        assert e.value.code == "preset_invalid" and e.value.extra["errors"]


def test_genre_validation_strict_but_keeps_extras():
    g = {"episode_s": [60, 90], "clip_s": [6, 10], "clips": [12, 18], "transition": "cut", "my_extra": {"a": 1}}
    assert presets.validate_data("genre", g)["my_extra"] == {"a": 1}
    for bad in ({**g, "episode_s": [90, 60]}, {**g, "transition": "spin"}, {**g, "clips": [1]},
                {**g, "hook": {"window_s": 3, "visual_in_s": 5}}):
        with pytest.raises(ValidationFailed):
            presets.validate_data("genre", bad)


def test_other_kinds_only_need_an_object():
    assert presets.validate_data("workflow", {"steps": ["a"]}) == {"steps": ["a"]}
    with pytest.raises(ValidationFailed):
        presets.validate_data("workflow", ["a"])  # type: ignore[arg-type]
    with pytest.raises(ValidationFailed) as e:
        presets.validate_data("bogus", {})
    assert e.value.code == "preset_kind_invalid"


# --- Seed idempotent + giữ chỉnh sửa --------------------------------------------------------------------------------


def test_seed_idempotent_and_lazy(services):
    with services.db.session() as s:
        assert s.query(Preset).count() == 0  # không có tác dụng phụ trước khi gọi
    presets.ensure_builtins(services)
    presets.ensure_builtins(services)
    presets.seed_builtins(services.db)  # gọi thẳng lần nữa: không nhân đôi
    with services.db.session() as s:
        rows = s.query(Preset).all()
        assert len(rows) == len(presets.builtin_definitions()) >= 10 and all(r.builtin and r.builtin_hash and r.project_id is None for r in rows)


def test_seed_upgrades_unedited_keeps_edited(services, owner_ctx, monkeypatch):
    presets.ensure_builtins(services)
    defs = presets.builtin_definitions()

    def newer():
        out = copy.deepcopy(defs)
        for d in out:
            if d["kind"] == "genre":
                d["data"]["clips"] = [3, 4]
        return out

    presets.update(owner_ctx, kind="genre", key="serial",
                   data={**spec_g(defs, "serial"), "clips": [20, 30]})  # người dùng sửa serial
    monkeypatch.setattr(presets, "builtin_definitions", newer)
    presets.seed_builtins(services.db)
    assert presets.get_preset(owner_ctx, kind="genre", key="series")["data"]["clips"] == [3, 4]  # chưa sửa → nâng cấp
    serial = presets.get_preset(owner_ctx, kind="genre", key="serial")
    assert serial["data"]["clips"] == [20, 30] and serial["modified"] is True  # đã sửa → giữ nguyên
    monkeypatch.undo()
    reset = presets.reset_builtin(owner_ctx, "serial")
    assert reset["data"]["clips"] == [12, 18] and reset["modified"] is False


def spec_g(defs, key):
    return next(d["data"] for d in defs if d["kind"] == "genre" and d["key"] == key)


def test_removed_builtin_is_not_resurrected(services, owner_ctx):
    presets.ensure_builtins(services)
    with services.db.session() as s:
        row = s.query(Preset).filter_by(kind="genre", key="montage").one()
        row.deleted_at = row.created_at
    presets.seed_builtins(services.db)
    assert not [p for p in presets.list_presets(owner_ctx, "genre") if p["key"] == "montage"]
    assert presets.reset_builtin(owner_ctx, "montage")["key"] == "montage"  # reset_builtin hồi sinh có chủ ý
    assert [p for p in presets.list_presets(owner_ctx, "genre") if p["key"] == "montage"]


# --- Ops preset.* ---------------------------------------------------------------------------------------------------


def test_preset_ops_crud_flow(client):
    lst = call(client, "preset.list", {"kind": "platform"}).json()["items"]
    assert {p["key"] for p in lst} == set(PLATFORMS)
    got = call(client, "preset.get", {"kind": "platform", "key": "tiktok"}).json()
    assert got["builtin"] and got["data"]["width"] == 1080 and got["modified"] is False

    dup = call(client, "preset.duplicate", {"id": got["id"], "key": "tiktok-nho", "name": "TikTok nhỏ"}).json()
    assert dup["builtin"] is False and dup["key"] == "tiktok-nho"
    assert call(client, "preset.duplicate", {"id": got["id"], "key": "tiktok-nho"}).json()["error_code"] == "preset_key_taken"

    newdata = {**dup["data"], "hashtags": {"max": 3, "max_shown": None}}
    up = call(client, "preset.update", {"id": dup["id"], "data": newdata}).json()
    assert up["data"]["hashtags"]["max"] == 3
    bad = call(client, "preset.update", {"id": dup["id"], "data": {**newdata, "width": 5}})
    assert bad.status_code == 422 and bad.json()["error_code"] == "preset_invalid"
    stale = call(client, "preset.update", {"id": dup["id"], "name": "x", "expected_updated_at": "2020-01-01T00:00:00Z"})
    assert stale.json()["error_code"] == "stale_write"

    created = call(client, "preset.create", {"kind": "workflow", "key": "wf1", "name": "Quy trình", "data": {"steps": []}})
    assert created.status_code == 200
    assert call(client, "preset.create", {"kind": "workflow", "key": "Bad Key", "name": "x", "data": {}}).status_code == 422

    prev = call(client, "preset.delete_preview", {"id": dup["id"]}).json()
    assert prev["impact"]["count"] == 1 and prev["confirm_token"]
    assert call(client, "preset.delete", {}).json()["error_code"] == "confirm_required"
    assert call(client, "preset.delete", {"confirm_token": prev["confirm_token"]}).status_code == 200
    assert call(client, "preset.get", {"id": dup["id"]}).status_code == 404
    keys = {p["key"] for p in call(client, "preset.list", {"kind": "platform"}).json()["items"]}
    assert "tiktok-nho" not in keys


def test_builtin_cannot_be_deleted_and_reset(client):
    tid = call(client, "preset.get", {"kind": "platform", "key": "yt-long"}).json()["id"]
    r = call(client, "preset.delete_preview", {"id": tid})
    assert r.status_code == 403 and r.json()["error_code"] == "preset_builtin"
    spec = call(client, "preset.get", {"id": tid}).json()["data"]
    spec["duration_s"] = {"min": 1, "max": 600, "recommended": [60, 300]}
    assert call(client, "preset.update", {"id": tid, "data": spec}).json()["modified"] is True
    back = call(client, "preset.reset_builtin", {"key": "yt-long"}).json()
    assert back["modified"] is False and back["data"]["duration_s"]["max"] == 43200
    assert call(client, "preset.reset_builtin", {"key": "khong-co"}).json()["error_code"] == "preset_not_builtin"


def test_project_scoped_preset_overrides_global(client):
    pid = call(client, "project.create", {"name": "P", "key": "p"}).json()["id"]
    tid = call(client, "preset.get", {"kind": "platform", "key": "tiktok"}).json()["id"]
    mine = call(client, "preset.duplicate", {"id": tid, "key": "tiktok", "to_project": True, "project_id": pid}).json()
    assert mine["project_id"] == pid
    lst = call(client, "preset.list", {"kind": "platform", "project_id": pid}).json()["items"]
    assert next(p for p in lst if p["key"] == "tiktok")["id"] == mine["id"] and len(lst) == 5
    assert len(call(client, "preset.list", {"kind": "platform"}).json()["items"]) == 5


def test_viewer_can_read_presets_but_not_write():
    from sagareel.core import ops

    for name in ("preset.list", "preset.get", "export.list", "export.check"):
        assert ops.REGISTRY[name].viewer
    for name in ("preset.create", "preset.update", "preset.delete", "preset.reset_builtin", "export.create", "export.open_folder"):
        assert not ops.REGISTRY[name].viewer
    assert ops.REGISTRY["preset.delete"].destructive


# --- Phép tính thuần: nền tảng --------------------------------------------------------------------------------------


def test_ffmpeg_args_follow_post_production_table():
    a = platforms.ffmpeg_args(spec_of("fb-reels"))
    pairs = dict(zip(a[::2], a[1::2], strict=False))
    assert pairs["-crf"] == "18" and pairs["-maxrate"] == "10000k" and pairs["-g"] == "60" and pairs["-keyint_min"] == "60"
    assert pairs["-b:a"] == "192k" and pairs["-ar"] == "48000" and pairs["-movflags"] == "+faststart"
    y = platforms.ffmpeg_args(spec_of("yt-long"))
    assert y[y.index("-g") + 1] == "15" and "open-gop=0" in y and y[y.index("-b:a") + 1] == "384k"
    assert y[y.index("-crf") + 1] == "17"


def test_safe_zone_scales_with_frame():
    s = spec_of("fb-reels")
    assert platforms.safe_zone(s) == {"top": 269, "bottom": 672, "left": 65, "right": 65}
    half = platforms.safe_zone(s, 540, 960)
    assert half == {"top": 134, "bottom": 336, "left": 32, "right": 32} or half["top"] == 135
    assert platforms.safe_zone(spec_of("yt-long")) is None


def test_plan_parts():
    assert platforms.plan_parts(60_000, 90) == [(0, 60_000)]
    parts = platforms.plan_parts(200_000, 90)
    assert len(parts) == 3 and parts[0][0] == 0 and parts[-1][1] == 200_000
    assert all(b - a <= 90_000 for a, b in parts) and all(x[1] == y[0] for x, y in itertools.pairwise(parts))
    snapped = platforms.plan_parts(200_000, 90, boundaries_ms=[70_000, 130_000, 50_000])
    assert snapped[0][1] in (70_000, 130_000, 50_000) and all(b - a <= 90_000 for a, b in snapped)
    assert platforms.part_label(1, 3) == "Phần 1/3"
    for total in (91_000, 180_001, 359_999):  # luôn <= max, phủ kín
        ps = platforms.plan_parts(total, 90, min_s=10)
        assert ps[0][0] == 0 and ps[-1][1] == total and all(0 < b - a <= 90_000 for a, b in ps)


# --- Phép tính thuần: kiểm tra --------------------------------------------------------------------------------------


def codes(report: dict) -> tuple[list[str], list[str]]:
    return [i["code"] for i in report["errors"]], [i["code"] for i in report["warnings"]]


def test_check_duration_and_size():
    s = spec_of("fb-reels")
    assert codes(checks.run_checks(s, duration_s=120, measure_loudness=False))[0] == ["duration_over_max"]
    e, w = codes(checks.run_checks(s, duration_s=120, split_parts=True, measure_loudness=False))
    assert e == [] and "duration_will_split" in w
    assert codes(checks.run_checks(s, duration_s=1, measure_loudness=False))[0] == ["duration_under_min"]
    assert "duration_outside_recommended" in codes(checks.run_checks(s, duration_s=80, measure_loudness=False))[1]
    assert codes(checks.run_checks(s, duration_s=30, measure_loudness=False)) == ([], [])
    big = checks.run_checks(s, duration_s=30, size_bytes=5 * 1024**3, measure_loudness=False)
    assert codes(big)[0] == ["size_over_max"]


def test_check_loudness():
    s = spec_of("tiktok")
    assert codes(checks.run_checks(s, duration_s=30, loudness={"integrated_lufs": -14.2, "true_peak_db": -1.2}))[1] == []
    _, w = codes(checks.run_checks(s, duration_s=30, loudness={"lufs": -20.0, "true_peak": 1.0}))
    assert w == ["loudness_off_target", "true_peak_high"]
    assert codes(checks.run_checks(s, duration_s=30, loudness=None))[1] == ["loudness_not_measured"]
    assert checks.normalize_loudness({"input_i": "-13.5", "input_tp": "-1.0"}) == {"lufs": -13.5, "true_peak": -1.0, "lra": None}


def test_check_subtitle_boxes_outside_safe_zone():
    s = spec_of("fb-reels")
    inside = {"x": 100, "y": 400, "w": 800, "h": 120, "start_ms": 1000}
    low = {"x": 100, "y": 1500, "w": 800, "h": 120, "start_ms": 4000}  # đè vùng dưới 672 px
    assert checks.check_subtitle_boxes(s, [inside], (1080, 1920)) == []
    r = checks.check_subtitle_boxes(s, [inside, low], (1080, 1920))
    assert r[0]["code"] == "subtitle_outside_safe_zone" and r[0]["count"] == 1 and "4s" in r[0]["message"]
    assert checks.check_subtitle_boxes(spec_of("yt-long"), [low], (1920, 1080)) == []  # không có vùng an toàn
    assert checks.check_subtitle_boxes(s, [(100, 1500, 800, 120)], (1080, 1920))[0]["count"] == 1  # dạng tuple


def test_check_metadata_limits_per_platform():
    yt = spec_of("yt-shorts")
    md = {"title": "a" * 101, "description": "x", "hashtags": ["#a"] * 4, "keywords": ["k" * 300, "m" * 300]}
    e, _w = codes({"errors": checks.check_metadata(yt, md), "warnings": []})
    assert {"title_too_long", "keywords_too_long"} <= set(e)
    assert [i["code"] for i in checks.check_metadata(yt, {"title": "ok", "hashtags": ["#a"] * 4})] == ["hashtags_hidden"]
    assert [i["code"] for i in checks.check_metadata(yt, {"title": "a<b"})] == ["title_forbidden_chars"]
    vi = "ế" * 2000  # 3 byte mỗi ký tự → 6000 byte > 5000
    assert [i["code"] for i in checks.check_metadata(yt, {"title": "t", "description": vi})] == ["caption_too_long"]
    tt = spec_of("tiktok")
    assert [i["code"] for i in checks.check_metadata(tt, {"title": "t", "description": "😀" * 1200})] == ["caption_too_long"]  # 2 UTF-16 mỗi emoji
    assert [i["code"] for i in checks.check_metadata(tt, {"title": "t", "hashtags": ["#a"] * 6})] == ["hashtags_too_many"]


def test_check_music_license():
    e, w = checks.check_music([{"title": "A", "license": "", "sound_id": 1},
                               {"title": "B", "license": "CC-BY 4.0", "attribution": "", "sound_id": 2},
                               {"title": "C", "license": "royalty-free", "sound_id": 3}])
    assert [i["code"] for i in e] == ["music_license_missing"] and e[0]["sound_id"] == 1
    assert [i["code"] for i in w] == ["music_attribution_missing"]


# --- Phép tính thuần: metadata --------------------------------------------------------------------------------------


def test_truncate_and_hashtag_nfc():
    assert metadata.truncate("abcdef", {"max_chars": 4}) == "abc…"
    assert metadata.truncate("ok", {"max_chars": 4}) == "ok"
    t = metadata.truncate("Việt Nam " * 20, {"max_bytes": 40})
    assert len(t.encode("utf-8")) <= 40 and t.endswith("…")
    assert metadata.hashtag("Phim cổ tích!") == "#PhimCổTích"
    assert metadata.hashtag("   ") == ""
    decomposed = "Việt"  # NFD
    assert metadata.hashtag(decomposed) == "#Việt"


def test_build_chapters_rules():
    marks = [(0, "Mở đầu"), (5_000, "Ngắn"), (30_000, "Giữa"), (50_000, "Cuối"), (115_000, "Quá sát đuôi")]
    ch = metadata.build_chapters(marks, 120_000)
    assert [c["time"] for c in ch] == ["00:00", "00:30", "00:50"] and ch[0]["title"] == "Mở đầu"
    assert metadata.build_chapters([(0, "a"), (30_000, "b")], 60_000) == []  # < 3 chương → bỏ
    assert metadata.build_chapters([(2_000, "a"), (30_000, "b"), (60_000, "c")], 90_000)[0]["time"] == "00:00"
    assert metadata.fmt_ts(3_725_000) == "1:02:05"
    assert metadata.chapters_text(ch).splitlines()[1] == "00:30 Giữa"


def test_template_metadata_and_finalize_respects_platform():
    proj = {"name": "Truyện cổ tích", "genre_key": "narration", "description": ""}
    seq = {"title": "Tập một: " + "rất dài " * 30, "synopsis": "Tóm tắt", "number": 1}
    base = metadata.template_metadata(project=proj, sequence=seq, lang="vi")
    assert base["source"] == "template" and base["pinned_comment"].startswith("Bạn")
    md = metadata.finalize(spec_of("yt-shorts"), base)
    assert len(md["title"]) <= 100 and md["hashtags"][0] == "#TruyệnCổTích"
    assert checks.check_metadata(spec_of("yt-shorts"), md) == []
    md_tt = metadata.finalize(spec_of("tiktok"), {**base, "description": "x" * 5000})
    assert checks.check_metadata(spec_of("tiktok"), md_tt) == []
    assert metadata.episode_label(3, (2, 3)) == "Tập 3 / Phần 2" and metadata.episode_label(3, None) == "Tập 3"


def test_part_variant_title_and_chapters():
    yt = spec_of("yt-long")
    md = {"title": "Tên", "description": "m", "hashtags": [], "chapters": [], "episode_label": "Tập 2", "part": None}
    chap = [{"start_ms": 0, "time": "00:00", "title": "a"}]
    v = metadata.part_variant(yt, md, 2, 3, chap)
    assert v["title"] == "Tên (Phần 2/3)" and v["part"] == {"index": 2, "total": 3} and v["episode_label"] == "Tập 2 / Phần 2"
    assert v["description_with_chapters"].startswith("00:00 a")


def test_unknown_platform_error(services):
    with pytest.raises(AppError) as e:
        platforms.get(services, "myspace")
    assert e.value.code == "unknown_platform" and e.value.status == 404
    assert isinstance(Forbidden("x"), AppError)
