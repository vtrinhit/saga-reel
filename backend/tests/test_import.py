"""Task C (M2): nhập clip hàng loạt — import.preview / import.apply."""

from __future__ import annotations

import io
import time
from pathlib import Path

import pytest
from PIL import Image
from sagareel.core.errors import AppError
from sagareel.db.models import (
    Asset,
    AssetVariant,
    ChangeSet,
    Clip,
    DialogueLine,
    Scene,
    Sequence,
    SystemState,
)
from sagareel.services import importer, media
from sqlalchemy import func, select

TEMPLATES = Path(__file__).resolve().parents[2] / "docs" / "import-templates"


@pytest.fixture
def ctx(owner_ctx):
    return owner_ctx


@pytest.fixture
def seq(ctx, project) -> int:
    with ctx.uow(op_name="test.create") as s:
        sq = Sequence(project_id=project, key="ep1", title="Tập 1")
        s.add(sq)
        s.flush()
        return sq.id


@pytest.fixture
def cast(ctx, project) -> dict[str, int]:
    """Asset AN (có look áo_mưa) và MINH."""
    with ctx.uow(op_name="test.create") as s:
        an = Asset(project_id=project, kind="character", key="an", name="Hà An")
        minh = Asset(project_id=project, kind="character", key="minh", name="Minh")
        s.add_all([an, minh])
        s.flush()
        v = AssetVariant(asset_id=an.id, key="ao_mua", name="Áo mưa")
        s.add(v)
        s.flush()
        return {"an": an.id, "minh": minh.id, "ao_mua": v.id}


def _count(ctx, model) -> int:
    with ctx.services.db.session() as s:
        return s.scalar(select(func.count()).select_from(model))


def _prev(ctx, seq_id, kind, text=None, **kw):
    src = {"kind": kind, **({"text": text} if text is not None else {}), **kw}
    return importer.preview(ctx.services, sequence_id=seq_id, source=src, mapping=kw.pop("mapping", None))


def _apply(ctx, seq_id, token, **kw):
    args = {"edits": None, "create_assets": False, "after_clip_id": None, "skip_rows": None, "skip_errors": False}
    args.update(kw)
    return importer.apply(ctx, sequence_id=seq_id, preview_token=token, **args)


LINES = """\
Cảnh 1: Quán cà phê
Toàn cảnh quán cà phê đêm mưa
Cận cảnh An bên cửa sổ
AN: Anh ấy lại không đến.
1. Minh bước vào
MINH(thở dốc): Xin lỗi, anh đến muộn.

Cảnh 2
- Ban công sau cơn mưa
NR: Và đêm mưa ấy trở thành kỷ niệm.
"""


def test_lines_preview_parses_and_never_writes(ctx, seq, cast):
    cs = _count(ctx, ChangeSet)
    p = _prev(ctx, seq, "lines", LINES)
    assert [r["fields"]["image_prompt"] for r in p["rows"]] == [
        "Toàn cảnh quán cà phê đêm mưa", "Cận cảnh An bên cửa sổ", "Minh bước vào", "Ban công sau cơn mưa"]
    assert [r["fields"]["scene_key"] for r in p["rows"]] == ["s1", "s1", "s1", "s2"]
    assert p["rows"][1]["fields"]["dialogue"][0]["speaker"] == "AN"
    d = p["rows"][2]["fields"]["dialogue"][0]
    assert d["speaker"] == "MINH" and d["emotion"] == "thở dốc"
    assert p["rows"][3]["fields"]["dialogue"][0]["speaker"] == ""  # NR = người kể
    assert p["preview_token"] and p["summary"]["rows"] == 4
    assert _count(ctx, Clip) == 0 and _count(ctx, Scene) == 0 and _count(ctx, ChangeSet) == cs


def test_clip_line_with_colon_prefix_is_not_dialogue(ctx, seq, cast):
    p = _prev(ctx, seq, "lines", "Cận cảnh: bàn tay run rẩy\nToàn cảnh phố")
    assert len(p["rows"]) == 2 and p["rows"][0]["fields"]["image_prompt"].startswith("Cận cảnh:")


def test_apply_lines_one_change_set(ctx, seq, cast):
    p = _prev(ctx, seq, "lines", LINES)
    before = _count(ctx, ChangeSet)
    r = _apply(ctx, seq, p["preview_token"])
    assert len(r["clip_ids"]) == 4 and r["dialogue_lines"] == 3 and len(r["scene_ids"]) == 2
    assert _count(ctx, ChangeSet) == before + 1 and r["change_set_id"]
    with ctx.services.db.session() as s:
        clips = s.scalars(select(Clip).where(Clip.sequence_id == seq).order_by(Clip.rank)).all()
        assert [c.id for c in clips] == r["clip_ids"]
        assert [c.key for c in clips] == ["c001", "c002", "c003", "c004"]
        assert clips[0].scene_id == clips[2].scene_id != clips[3].scene_id
        lines = s.scalars(select(DialogueLine).where(DialogueLine.clip_id == clips[2].id)).all()
        assert lines[0].speaker_asset_id == cast["minh"] and lines[0].emotion == "thở dốc"
        nr = s.scalars(select(DialogueLine).where(DialogueLine.clip_id == clips[3].id)).one()
        assert nr.speaker_asset_id is None
        assert s.scalar(select(Scene.title).where(Scene.id == clips[3].scene_id)) == "Cảnh 2"


def test_token_is_single_use_and_expires(ctx, seq):
    p = _prev(ctx, seq, "lines", "Một\nHai")
    _apply(ctx, seq, p["preview_token"])
    with pytest.raises(AppError) as e:
        _apply(ctx, seq, p["preview_token"])
    assert e.value.code == "preview_token_invalid" and _count(ctx, Clip) == 2

    p2 = _prev(ctx, seq, "lines", "Ba")
    with ctx.services.db.session() as s:
        row = s.scalars(select(SystemState).where(SystemState.key == importer.TOKEN_PREFIX + p2["preview_token"])).one()
        row.value = {**row.value, "created": time.time() - 3700}
    with pytest.raises(AppError) as e:
        _apply(ctx, seq, p2["preview_token"])
    assert e.value.code == "preview_token_invalid"
    with pytest.raises(AppError):
        _apply(ctx, seq + 99, p2["preview_token"])


def test_assets_and_looks(ctx, seq, cast):
    csv_text = "Cảnh,Nhân vật,Prompt ảnh,Thoại\nCảnh 1,\"AN:áo mưa, BI:áo đỏ, minh:không_có\",x,\"BI: chào\"\n"
    p = _prev(ctx, seq, "csv", csv_text)
    row = p["rows"][0]
    ch = row["fields"]["characters"]
    assert ch[0]["asset_id"] == cast["an"] and ch[0]["variant_id"] == cast["ao_mua"]  # look khớp không phân biệt dấu/hoa
    assert ch[1]["asset_id"] is None and ch[2]["variant_id"] is None
    assert any("không_có" in w for w in row["warnings"]) and any("'BI'" in w for w in row["warnings"])
    assert [a["code"] for a in p["assets_to_create"]] == ["BI"] and p["assets_to_create"][0]["rows"] == [0]

    # không tạo asset → bỏ qua mã thiếu
    r = _apply(ctx, seq, p["preview_token"])
    assert r["unresolved_assets"] == ["BI"] and r["created_assets"] == []
    with ctx.services.db.session() as s:
        c = s.get(Clip, r["clip_ids"][0])
        assert [a["asset_id"] for a in c.assets] == [cast["an"], cast["minh"]]
        assert c.assets[0]["variant_id"] == cast["ao_mua"] and "variant_id" not in c.assets[1]

    # tạo asset
    p = _prev(ctx, seq, "csv", csv_text)
    r = _apply(ctx, seq, p["preview_token"], create_assets=True)
    assert [a["key"] for a in r["created_assets"]] == ["BI"] and r["unresolved_assets"] == []
    with ctx.services.db.session() as s:
        bi = s.scalars(select(Asset).where(Asset.key == "BI")).one()
        c = s.get(Clip, r["clip_ids"][0])
        assert bi.id in [a["asset_id"] for a in c.assets]
        d = s.scalars(select(DialogueLine).where(DialogueLine.clip_id == c.id)).one()
        assert d.speaker_asset_id == bi.id


def test_csv_bom_crlf_semicolon_empty_rows_unknown_column(ctx, seq):
    text = ("﻿CẢNH;Prompt Ảnh;Thời Lượng;Cột lạ;STT\r\n"
            "Cảnh 1;\"Dòng một\r\nxuống dòng\";6,5;x;1\r\n"
            ";;;;\r\n"
            "\r\n"
            ";Dòng hai;abc;y;2\r\n"
            ";Dòng ba;7s;z;3\r\n")
    p = _prev(ctx, seq, "csv", text)
    assert p["mapping"] == {"CẢNH": "scene", "Prompt Ảnh": "image_prompt", "Thời Lượng": "duration", "Cột lạ": None,
                            "STT": None}
    assert any("Cột lạ" in w for w in p["warnings"]) and not any("STT" in w for w in p["warnings"])
    rows = p["rows"]
    assert len(rows) == 3 and rows[0]["fields"]["image_prompt"] == "Dòng một\nxuống dòng"
    assert rows[0]["fields"]["duration_ms"] == 6500 and rows[2]["fields"]["duration_ms"] == 7000
    assert rows[1]["errors"] and "abc" in rows[1]["errors"][0] and rows[1]["fields"]["duration_ms"] is None
    assert [r["source_row"] for r in rows] == [2, 5, 6] or rows[1]["source_row"] > rows[0]["source_row"]
    assert all(r["fields"]["scene_key"] == "s1" for r in rows)  # ô cảnh trống = vẫn cảnh trước

    with pytest.raises(AppError) as e:
        _apply(ctx, seq, p["preview_token"])
    assert e.value.code == "import_has_errors" and e.value.extra["rows"][0]["index"] == 1 and _count(ctx, Clip) == 0
    r = _apply(ctx, seq, p["preview_token"], skip_errors=True)
    assert len(r["clip_ids"]) == 2 and r["skipped"][0]["index"] == 1


def test_edits_fix_error_rows_and_skip_rows(ctx, seq):
    p = _prev(ctx, seq, "csv", "image,duration\nA,xx\nB,5\nC,6\n")
    r = _apply(ctx, seq, p["preview_token"], edits=[{"index": 0, "fields": {"duration": "4.5", "notes": "đã sửa"}}],
               skip_rows=[2])
    assert len(r["clip_ids"]) == 2
    with ctx.services.db.session() as s:
        cs = s.scalars(select(Clip).order_by(Clip.rank)).all()
        assert (cs[0].duration_ms, cs[0].notes, cs[0].image_prompt) == (4500, "đã sửa", "A")
        assert cs[1].duration_ms == 5000


@pytest.mark.parametrize("v,ms", [("6", 6000), ("6,5", 6500), ("6s", 6000), (" 6 giây", 6000), ("0:06", 6000),
                                  ("1:02", 62000), ("6000ms", 6000), (6.5, 6500)])
def test_parse_duration(v, ms):
    assert importer.parse_duration(v) == ms


@pytest.mark.parametrize("v", ["abc", "0", "-3", "9999", "", "1:2:3:4"])
def test_parse_duration_bad(v):
    with pytest.raises(ValueError):
        importer.parse_duration(v)


def test_alias_and_enum_values(ctx, seq):
    text = ("nhân vật,PROMPT VIDEO,Lời thoại,Kiểu nối,Engine,Chế độ prompt,Cỡ cảnh,Chuyển động máy,Ghi chú,Provider,Model\n"
            "AN,chạy,\"MINH: ê\",Tiếp,ảnh động,nguyên văn,Cận,Pan,note,grok,m1\n"
            "AN,x,,bậy,video,sync,,,,,\n")
    p = _prev(ctx, seq, "csv", text)
    f = p["rows"][0]["fields"]
    assert (f["link"], f["engine"], f["prompt_mode"]) == ("continue", "still_motion", "verbatim")
    assert (f["motion_prompt"], f["shot_size"], f["camera_move"], f["notes"]) == ("chạy", "Cận", "Pan", "note")
    assert p["rows"][1]["errors"] and "bậy" in p["rows"][1]["errors"][0] and p["rows"][1]["fields"]["engine"] == "ai_video"
    r = _apply(ctx, seq, p["preview_token"], skip_errors=True)
    with ctx.services.db.session() as s:
        c = s.get(Clip, r["clip_ids"][0])
        assert (c.link, c.engine, c.prompt_mode) == ("continue", "still_motion", "verbatim")
        assert (c.connection, c.model, c.settings) == ("grok", "m1", {})


def test_explicit_mapping_overrides_guess(ctx, seq):
    p = importer.preview(ctx.services, sequence_id=seq, source={"kind": "csv", "text": "ý tưởng,xyz\nhình ảnh X,thoại Y\n"},
                         mapping=None)
    assert p["rows"] == [] and any("Không nhận ra" in w for w in p["warnings"])
    p = importer.preview(ctx.services, sequence_id=seq, source={"kind": "csv", "text": "ý tưởng,xyz\nhình ảnh X,thoại Y\n"},
                         mapping={"ý tưởng": "image_prompt", "xyz": "notes"})
    assert p["rows"][0]["fields"]["image_prompt"] == "hình ảnh X" and p["rows"][0]["fields"]["notes"] == "thoại Y"
    with pytest.raises(AppError) as e:
        importer.preview(ctx.services, sequence_id=seq, source={"kind": "csv", "text": "a\nb\n"}, mapping={"a": "bogus"})
    assert e.value.code == "import_mapping_invalid"


def test_xlsx_via_upload(ctx, seq, cast, settings):
    from openpyxl import Workbook
    from sagareel.services import uploads

    wb = Workbook()
    ws = wb.active
    ws.append(["Cảnh", "Nhân vật", "Prompt ảnh", "Thoại", "Thời lượng"])
    ws.append(["Cảnh 1: Mở đầu", "AN", "Ảnh một", "AN: chào", 5])
    ws.append([None, None, None, None, None])
    ws.append([None, "AN, MINH", "Ảnh hai", None, 6.5])
    buf = io.BytesIO()
    wb.save(buf)
    data = buf.getvalue()
    st = uploads.start(ctx.services, name="clips.xlsx", size=len(data), kind="other")
    uploads.append(ctx.services, st["id"], 0, data)
    p = importer.preview(ctx.services, sequence_id=seq, source={"kind": "xlsx", "upload_id": st["id"]}, mapping=None)
    assert len(p["rows"]) == 2 and p["rows"][0]["fields"]["duration_ms"] == 5000
    assert p["rows"][1]["fields"]["duration_ms"] == 6500 and p["rows"][1]["fields"]["scene_key"] == "s1"
    assert p["rows"][1]["source_row"] == 4
    with pytest.raises(AppError) as e:
        importer.preview(ctx.services, sequence_id=seq, source={"kind": "xlsx", "text": "x"}, mapping=None)
    assert e.value.code == "import_source_missing"


def test_xlsx_corrupt(ctx, seq):
    from sagareel.services import uploads

    st = uploads.start(ctx.services, name="bad.xlsx", size=10, kind="other")
    uploads.append(ctx.services, st["id"], 0, b"not a zip!")
    with pytest.raises(AppError) as e:
        importer.preview(ctx.services, sequence_id=seq, source={"kind": "xlsx", "upload_id": st["id"]}, mapping=None)
    assert e.value.code == "import_file_invalid"


def test_json_and_yaml_formats(ctx, seq, cast):
    j = '{"scenes":[{"scene":"Cảnh 3: Đêm","clips":[{"Prompt ảnh":"a","Thoại":["AN: một","hai"],"nhân vật":["AN:Áo Mưa"]}]}]}'
    p = _prev(ctx, seq, "json", j)
    f = p["rows"][0]["fields"]
    assert f["scene_key"] == "s3" and f["scene_title"] == "Đêm"
    assert [d["speaker"] for d in f["dialogue"]] == ["AN", ""] and f["characters"][0]["variant_id"] == cast["ao_mua"]
    p = _prev(ctx, seq, "yaml", "- image_prompt: a\n  duration: 5\n- image_prompt: b\n  duration: '0:07'\n")
    assert [r["fields"]["duration_ms"] for r in p["rows"]] == [5000, 7000]
    for kind, bad in (("json", "{oops"), ("yaml", "a: [1\n"), ("json", "42")):
        with pytest.raises(AppError) as e:
            _prev(ctx, seq, kind, bad)
        assert e.value.code in ("import_parse_failed", "import_format_invalid")


@pytest.mark.parametrize("name,kind", [("clips.csv", "csv"), ("clips.json", "json"), ("clips.yaml", "yaml")])
def test_templates_text(ctx, seq, cast, name, kind):
    raw = (TEMPLATES / name).read_bytes()
    text = raw.decode("utf-8-sig") if kind == "csv" else raw.decode("utf-8")
    p = _prev(ctx, seq, kind, text)
    _check_template(p)


def test_template_xlsx(ctx, seq, cast):
    from sagareel.services import uploads

    data = (TEMPLATES / "clips.xlsx").read_bytes()
    st = uploads.start(ctx.services, name="clips.xlsx", size=len(data), kind="other")
    uploads.append(ctx.services, st["id"], 0, data)
    _check_template(importer.preview(ctx.services, sequence_id=seq, source={"kind": "xlsx", "upload_id": st["id"]},
                                     mapping=None))


def _check_template(p):
    assert len(p["rows"]) == 6 and not any(r["errors"] for r in p["rows"]), p["rows"]
    assert [r["fields"]["scene_key"] for r in p["rows"]] == ["s1", "s1", "s1", "s2", "s2", "s2"]
    assert [r["fields"]["duration_ms"] for r in p["rows"]] == [5000, 6000, 7000, 6000, 4000, 6000]
    assert p["rows"][2]["fields"]["dialogue"][1]["speaker"] == "AN" and len(p["rows"][2]["fields"]["dialogue"]) == 2
    assert p["rows"][5]["fields"]["dialogue"][0]["speaker"] == ""
    assert not p["assets_to_create"] and not [w for w in p["warnings"] if w.startswith("Cột")]


def test_folder_one_clip_per_image(ctx, seq, project, settings, tmp_path):
    ids = []
    for name in ("shot10.png", "shot2.png", "shot1.png"):
        f = tmp_path / name
        Image.new("RGB", (32, 32), (len(ids) * 40, 5, 5)).save(f)
        with ctx.services.db.session() as s:
            ids.append(media.register(s, f, kind="image", origin="upload", settings=settings,
                                      project_id=project, meta={"name": name}).id)
    with ctx.services.db.session() as s:  # media không phải ảnh
        f = tmp_path / "a.txt"
        f.write_text("x")
        txt = media.register(s, f, kind="other", origin="upload", settings=settings).id
    p = importer.preview(ctx.services, sequence_id=seq, source={"kind": "folder", "media_ids": ids + [txt, 99999]},
                         mapping=None)
    imgs = [r for r in p["rows"] if not r["errors"]]
    assert [r["fields"]["title"] for r in imgs] == ["shot1", "shot2", "shot10"]  # sắp tự nhiên theo tên
    assert len(p["rows"]) == 5 and sum(1 for r in p["rows"] if r["errors"]) == 2
    r = _apply(ctx, seq, p["preview_token"], skip_errors=True)
    with ctx.services.db.session() as s:
        assert [s.get(Clip, i).input_media_id for i in r["clip_ids"]] == [ids[2], ids[1], ids[0]]


def test_insert_after_clip_keeps_order(ctx, seq):
    first = _apply(ctx, seq, _prev(ctx, seq, "lines", "A\nB\nC")["preview_token"])["clip_ids"]
    ins = _apply(ctx, seq, _prev(ctx, seq, "lines", "X\nY\nZ\nW\nV")["preview_token"], after_clip_id=first[0])["clip_ids"]
    tail = _apply(ctx, seq, _prev(ctx, seq, "lines", "T")["preview_token"], after_clip_id=first[2])["clip_ids"]
    with ctx.services.db.session() as s:
        order = [c.image_prompt for c in s.scalars(select(Clip).where(Clip.sequence_id == seq).order_by(Clip.rank))]
    assert order == ["A", "X", "Y", "Z", "W", "V", "B", "C", "T"] and len(ins) == 5 and len(tail) == 1
    with pytest.raises(AppError) as e:
        _apply(ctx, seq, _prev(ctx, seq, "lines", "Q")["preview_token"], after_clip_id=424242)
    assert e.value.code == "clip_not_found"


def test_existing_scene_reused_and_keys_do_not_collide_with_deleted(ctx, seq):
    _apply(ctx, seq, _prev(ctx, seq, "lines", "Cảnh 1: A\nx")["preview_token"])
    r = _apply(ctx, seq, _prev(ctx, seq, "lines", "Cảnh 1: A\ny")["preview_token"])
    assert r["scene_ids"] == []  # dùng lại scene s1
    with ctx.services.db.session() as s:
        first = s.scalars(select(Clip).order_by(Clip.id)).first()
        first.deleted_at = first.created_at  # xoá mềm c001: key vẫn bị giữ
    r = _apply(ctx, seq, _prev(ctx, seq, "lines", "z")["preview_token"])
    with ctx.services.db.session() as s:
        assert s.get(Clip, r["clip_ids"][0]).key == "c003"


def test_unknown_sequence(ctx):
    with pytest.raises(AppError) as e:
        _prev(ctx, 12345, "lines", "a")
    assert e.value.code == "sequence_not_found"


def test_1000_rows_preview_fast(ctx, seq, cast):
    lines = ["Cảnh,Nhân vật,Prompt ảnh,Prompt video,Thoại,Thời lượng"]
    for i in range(1000):
        lines.append(f'Cảnh {i // 10 + 1},"AN, MINH","Prompt ảnh số {i} với vài từ tiếng Việt","chuyển động {i}","AN: câu {i}\nMINH: trả lời",{4 + i % 5}')
    text = "\n".join(lines)
    t0 = time.perf_counter()
    p = _prev(ctx, seq, "csv", text)
    dt = time.perf_counter() - t0
    assert len(p["rows"]) == 1000 and dt < 3, dt
    t0 = time.perf_counter()
    r = _apply(ctx, seq, p["preview_token"])
    assert len(r["clip_ids"]) == 1000 and len(r["scene_ids"]) == 100 and r["dialogue_lines"] == 2000
    assert time.perf_counter() - t0 < 30


def test_ops_over_rest(client, ctx, seq):
    r = client.post("/api/ops/import.preview", json={"sequence_id": seq, "source": {"kind": "lines", "text": "a\nb"}})
    assert r.status_code == 200, r.text
    tok = r.json()["preview_token"]
    r = client.post("/api/ops/import.apply", json={"sequence_id": seq, "preview_token": tok})
    assert r.status_code == 200 and len(r.json()["clip_ids"]) == 2
    r = client.post("/api/ops/import.apply", json={"sequence_id": seq, "preview_token": tok})
    assert r.status_code == 410 and r.json()["error_code"] == "preview_token_invalid"
    r = client.post("/api/ops/import.preview", json={"sequence_id": seq, "source": {"kind": "csv"}})
    assert r.status_code == 422
