"""Prompt compiler (Task A): verbatim từng byte, synced 4 khối, thứ tự/nhãn ref, cắt max_refs, chọn look, motion,
sheet, template là dữ liệu, khung cuối clip trước cho link=continue."""

from __future__ import annotations

import re
from pathlib import Path

import anyio
import pytest
from PIL import Image
from sqlalchemy import select

from sagareel.compile import compiler, contracts
from sagareel.core import rank
from sagareel.db.models import (
    Asset,
    AssetVariant,
    Clip,
    DialogueLine,
    Media,
    ProviderConnection,
    Scene,
    Sequence,
    Take,
    Template,
)
from sagareel.providers.base import Connection, ImageRequest
from sagareel.providers.fake.image import FakeImageAdapter
from sagareel.services import library, media

# Chữ Việt dựng sẵn (NFC) + dạng tổ hợp (NFD: "ê" = e + U+0302), CRLF, tab, khoảng trắng hai đầu.
NASTY = "  Ánh trăng rọi xuống mái nhà cũ — “Nguyễn Thị Ê”\r\nhai dòng\tcó tab, ê tổ hợp, 😀  \n"


# --- Dựng dữ liệu -------------------------------------------------------------------------------------------------


def _image(settings, tmp_path, name: str, color=(10, 20, 30)) -> Path:
    p = tmp_path / f"{name}.png"
    Image.new("RGB", (36, 64), color).save(p)
    return p


@pytest.fixture
def mk(owner_ctx, project, settings, tmp_path):
    """Bộ dựng dữ liệu trực tiếp trong DB (Task B chưa có op clip)."""
    db = owner_ctx.services.db
    counter = iter(range(10_000))

    class Maker:
        def image(self) -> int:
            n = next(counter)
            with db.session() as s:
                return media.register(s, _image(settings, tmp_path, f"img{n}", (n % 250, 40, 90)), kind="image",
                                      origin="upload", settings=settings, project_id=project).id

        def asset(self, kind: str, name: str, description: str = "", **look) -> tuple[int, int]:
            out = library.asset_create(owner_ctx, project, kind, name, description=description)
            look_id = out["looks"][0]["id"]
            if look:
                library.look_update(owner_ctx, look_id, look)
            return out["id"], look_id

        def sequence(self, number: int = 1) -> int:
            with db.session() as s:
                seq = Sequence(project_id=project, key=f"ep{number}-{next(counter)}", number=number,
                               title=f"Tập {number}")
                s.add(seq)
                s.flush()
                return seq.id

        def clip(self, seq_id: int, **values) -> int:
            with db.session() as s:
                last = s.scalars(select(Clip.rank).where(Clip.sequence_id == seq_id).order_by(Clip.rank.desc())
                                 ).first()
                c = Clip(sequence_id=seq_id, key=f"c{next(counter)}", rank=rank.between(last, None), **values)
                s.add(c)
                s.flush()
                return c.id

        def take(self, owner_id: int, part: str, media_id: int, owner_type: str = "clip") -> int:
            with db.session() as s:
                t = Take(owner_type=owner_type, owner_id=owner_id, part=part, media_id=media_id,
                         status="succeeded")
                s.add(t)
                s.flush()
                return t.id

        def connection(self, name: str = "fake-img", **options) -> int:
            with db.session() as s:
                row = ProviderConnection(kind="image", adapter="fake_image", name=name, concurrency=2,
                                         options=options)
                s.add(row)
                s.flush()
                return row.id

    return Maker()


def compile_image(owner_ctx, clip_id: int, connection=None) -> contracts.CompiledPrompt:
    with owner_ctx.services.db.session() as s:
        return contracts.compile_clip_image(s, clip_id, connection=connection)


# --- Verbatim (Review Focus 3) ------------------------------------------------------------------------------------


def test_verbatim_reaches_fake_image_adapter_byte_identical(owner_ctx, mk, tmp_path):
    seq = mk.sequence()
    char, _ = mk.asset("character", "An", "mô tả không được chèn")
    extra = mk.image()
    cid = mk.clip(seq, prompt_mode="verbatim", image_prompt=NASTY, motion_prompt=NASTY + "x",
                  assets=[{"asset_id": char}], extra_ref_media_ids=[extra])
    cp = compile_image(owner_ctx, cid)  # phiên mới: giá trị đọc lại từ DB
    assert cp.text == NASTY and cp.text.encode("utf-8") == NASTY.encode("utf-8")
    assert [(r.media_id, r.source) for r in cp.refs] == [(extra, "extra")]  # không ref thư viện
    with owner_ctx.services.db.session() as s:
        assert contracts.compile_clip_motion(s, cid).text == NASTY + "x"

    adapter = FakeImageAdapter(Connection(id=1, name="f", adapter="fake_image", kind="image", base_url="",
                                          api_key="", default_model="", options={"short_side": 32}))
    req = ImageRequest(prompt=cp.text, refs=[], aspect="9:16", resolution="1080p", model=None, quality=None,
                       out_dir=tmp_path / "out")
    result = anyio.run(adapter.generate, req)
    assert Path(result.meta["prompt_file"]).read_bytes() == NASTY.encode("utf-8")
    assert result.meta["prompt_received"] == NASTY


def test_verbatim_refs_input_then_extra_truncated(owner_ctx, mk):
    mk.connection("tiny", max_refs=1)
    seq = mk.sequence()
    a, b = mk.image(), mk.image()
    cid = mk.clip(seq, prompt_mode="verbatim", image_prompt="x", input_media_id=a, extra_ref_media_ids=[b])
    cp = compile_image(owner_ctx, cid, connection="tiny")
    assert [r.media_id for r in cp.refs] == [a] and cp.refs[0].label == "Image 1: subject of input image"
    assert any("tối đa 1" in w for w in cp.warnings)


# --- Synced -------------------------------------------------------------------------------------------------------


def test_synced_four_blocks_verbatim_locks_and_ref_order(owner_ctx, mk, project):
    mk.connection()
    desc = "  Cô gái 20 tuổi, tóc đen dài\nmắt nâu  "
    char, look = mk.asset("character", "An", desc, lock_word=" áo len xanh navy, mặt dây bạc ",
                          description_delta="Áo dài đỏ", keep_features=["nốt ruồi má trái"])
    loc, loc_look = mk.asset("location", "Căn nhà cũ", "Nhà gỗ ba gian")
    prop, prop_look = mk.asset("prop", "Chiếc nhẫn", "Nhẫn bạc khắc chữ")
    anchor, sheet_cu, sheet_34, loc_img, prop_img = (mk.image() for _ in range(5))
    library.look_update(owner_ctx, look, {"anchor_media_id": anchor,
                                          "sheet": {"closeup": sheet_cu, "three_quarter": sheet_34}})
    library.look_set_anchor(owner_ctx, loc_look, loc_img)
    library.look_set_anchor(owner_ctx, prop_look, prop_img)
    from sagareel.services import projects

    projects.update(owner_ctx, project, {"style_block": "Phong cách phim Việt 1990, film grain",
                                         "negative_block": "chữ, logo"})
    seq = mk.sequence()
    cid = mk.clip(seq, image_prompt="An đứng bên cửa sổ, tay cầm chiếc nhẫn", shot_size="cu", angle="low",
                  lens="85mm", assets=[{"asset_id": prop}, {"asset_id": loc}, {"asset_id": char}])
    cp = compile_image(owner_ctx, cid, connection="fake-img")
    text = cp.text
    # 4 khối theo thứ tự
    pos = [text.index(h) for h in ("[CHARACTER LOCK]", "[STYLE LOCK]", "[SCENE]", "[ACTION / CAMERA]")]
    assert pos == sorted(pos)
    # khối khoá chèn nguyên văn (cả khoảng trắng/xuống dòng)
    assert f"An: {desc}\n" in text
    assert "Keep exactly:  áo len xanh navy, mặt dây bạc ; face" in text
    assert "Áo dài đỏ" in text and "Must keep: nốt ruồi má trái." in text
    assert "Phong cách phim Việt 1990, film grain" in text and "Avoid: chữ, logo." in text
    assert "Close-up shot, low angle, shot on 85mm" in text and "Vertical 9:16" in text
    assert "An đứng bên cửa sổ, tay cầm chiếc nhẫn" in text
    # ref: nhân vật (mỏ neo + sheet cận mặt vì shot cu) → bối cảnh → đạo cụ
    assert [r.media_id for r in cp.refs] == [anchor, sheet_cu, loc_img, prop_img]
    assert [r.label for r in cp.refs] == ["Image 1: appearance of An", "Image 2: appearance of An",
                                          "Image 3: spatial_layout of Căn nhà cũ", "Image 4: subject of Chiếc nhẫn"]
    # chỉ số trong chữ khớp danh sách ref (hai chiều)
    in_text = re.findall(r"^(Image \d+: [^\n]*)\.$", text, flags=re.MULTILINE)
    assert in_text == [r.label for r in cp.refs]
    assert cp.provider_caps["connection"] == "fake-img" and cp.provider_caps["max_refs"] == 9


def test_max_refs_truncates_from_tail_with_warning(owner_ctx, mk):
    mk.connection("two", max_refs=2)
    char, look = mk.asset("character", "An", "d")
    loc, loc_look = mk.asset("location", "Chợ", "c")
    library.look_set_anchor(owner_ctx, look, mk.image())
    library.look_update(owner_ctx, look, {"sheet": {"three_quarter": mk.image()}})
    library.look_set_anchor(owner_ctx, loc_look, mk.image())
    seq = mk.sequence()
    cid = mk.clip(seq, image_prompt="p", assets=[{"asset_id": char}, {"asset_id": loc}])
    cp = compile_image(owner_ctx, cid, connection="two")
    assert len(cp.refs) == 2 and all("of An" in r.label for r in cp.refs)
    assert any("tối đa 2" in w and "spatial_layout of Chợ" in w for w in cp.warnings)
    assert "Image 3" not in cp.text


def test_look_selection_by_sequence_number_then_default(owner_ctx, mk):
    char, base = mk.asset("character", "An", "d", lock_word="BASE")
    young = library.look_create(owner_ctx, char, "15 tuổi", lock_word="YOUNG", valid_from=1, valid_to=3)["id"]
    library.look_create(owner_ctx, char, "Bị thương", lock_word="HURT", valid_from=4)["id"]
    explicit = library.look_create(owner_ctx, char, "Áo cưới", lock_word="WEDDING")["id"]
    expect = {1: "YOUNG", 3: "YOUNG", 4: "HURT", 9: "HURT"}
    for number, word in expect.items():
        cid = mk.clip(mk.sequence(number), image_prompt="p", assets=[{"asset_id": char}])
        assert f"Keep exactly: {word};" in compile_image(owner_ctx, cid).text, number
    cid = mk.clip(mk.sequence(2), image_prompt="p", assets=[{"asset_id": char, "variant_id": explicit}])
    assert "Keep exactly: WEDDING;" in compile_image(owner_ctx, cid).text
    # Không look nào có khoảng chứa tập 0 → look mặc định (base, look đầu tiên)
    library.look_update(owner_ctx, young, {"valid_from": 1})
    cid = mk.clip(mk.sequence(0), image_prompt="p", assets=[{"asset_id": char}])
    assert "Keep exactly: BASE;" in compile_image(owner_ctx, cid).text
    # Đổi mặc định → look mặc định mới thắng khi không khớp khoảng
    library.look_update(owner_ctx, explicit, {"is_default": True})
    with owner_ctx.services.db.session() as s:
        assert s.get(AssetVariant, base).is_default is False
    assert "Keep exactly: WEDDING;" in compile_image(owner_ctx, cid).text


def test_template_is_data_db_row_overrides_builtin(owner_ctx, mk):
    cid = mk.clip(mk.sequence(), image_prompt="  giữ nguyên  ")
    with owner_ctx.services.db.session() as s:
        s.add(Template(key="image.synced", kind="image", body="TỰ SỬA [{{ prompt }}] {{ aspect }}", locked=True))
    assert compile_image(owner_ctx, cid).text == "TỰ SỬA [  giữ nguyên  ] 9:16"


def test_motion_synced_beats_dialogue_verbatim(owner_ctx, mk):
    char, _ = mk.asset("character", "Linh", "d")
    seq = mk.sequence()
    cid = mk.clip(seq, motion_prompt="Linh đặt điện thoại xuống", duration_ms=10_000, camera_move="push_in",
                  beats=["turns to the door", {"text": "slowly sits down"}], sfx_desc="door creak")
    with owner_ctx.services.db.session() as s:
        s.add(DialogueLine(clip_id=cid, rank="a0", speaker_asset_id=char, text=" Anh về rồi à? ", source="grok",
                           emotion="calmly"))
        s.add(DialogueLine(clip_id=cid, rank="a1", text="lời TTS không chèn", source="tts"))
    with owner_ctx.services.db.session() as s:
        text = contracts.compile_clip_motion(s, cid).text
    assert text.startswith("Start from the provided image;")
    assert "Camera: slow push-in." in text and "Linh đặt điện thoại xuống" in text
    assert "[00:00-00:05] turns to the door" in text and "[00:05-00:10] slowly sits down" in text
    assert 'Dialogue: Linh says in Vietnamese (calmly), natural lip movement: " Anh về rồi à? "' in text
    assert "lời TTS" not in text and "SFX: door creak." in text and "no morphing" in text
    short = mk.clip(seq, motion_prompt="m", duration_ms=6000, beats=["one", "two"])
    with owner_ctx.services.db.session() as s:
        t2 = contracts.compile_clip_motion(s, short).text
    assert "\none\n" in t2 and "[00:" not in t2


def test_sheet_anchor_text_only_other_views_only_anchor(owner_ctx, mk):
    _char, look = mk.asset("character", "An", "Cô gái tóc đen", lock_word="áo len xanh")
    with owner_ctx.services.db.session() as s:
        cp = contracts.compile_sheet(s, look, "anchor")
    assert cp.refs == [] and "Full-body front view" in cp.text and "áo len xanh" in cp.text
    with owner_ctx.services.db.session() as s:
        cp = contracts.compile_sheet(s, look, "profile")
    assert cp.refs == [] and any("mỏ neo" in w for w in cp.warnings)
    anchor = mk.image()
    library.look_update(owner_ctx, look, {"anchor_media_id": anchor, "ref_media_ids": [mk.image()],
                                          "sheet": {"back": mk.image()}})
    with owner_ctx.services.db.session() as s:
        cp = contracts.compile_sheet(s, look, "profile")
    assert [(r.media_id, r.label) for r in cp.refs] == [(anchor, "Image 1: appearance of An")]
    assert cp.text.startswith("Image 1 is the anchor reference of An") and "side profile" in cp.text


def test_continue_uses_last_frame_of_previous_video_take(owner_ctx, mk, make_clip_tiny):
    db = owner_ctx.services.db
    seq = mk.sequence()
    prev = mk.clip(seq, image_prompt="a")
    video = make_clip_tiny(db)
    with db.session() as s:
        s.get(Clip, prev).video_take_id = mk.take(prev, "video", video)
    cid = mk.clip(seq, image_prompt="b", link="continue")
    cp = compile_image(owner_ctx, cid)
    assert cp.refs == [] and any("khung cuối" in w for w in cp.warnings)

    ids = anyio.run(compiler.prepare_clip, owner_ctx.services, cid)
    assert len(ids) == 1
    again = anyio.run(compiler.ensure_frame, owner_ctx.services, video)
    assert again == ids[0]  # dùng lại media dẫn xuất (không trích lại)
    with db.session() as s:
        frame = s.get(Media, ids[0])
        assert frame.kind == "image" and frame.origin == "derived" and frame.meta["derived_from"] == video
        assert (frame.width, frame.height) == (64, 112)
        assert media.file_path(owner_ctx.services.settings, frame).is_file()
    cp = compile_image(owner_ctx, cid)
    assert [(r.media_id, r.role, r.source) for r in cp.refs] == [(ids[0], "start_frame", "continue")]
    assert cp.refs[0].label.startswith("Image 1: start_frame of previous clip")
    assert cp.refs[0].label + "." in cp.text


def test_continue_without_previous_video_falls_back_to_image(owner_ctx, mk):
    seq = mk.sequence()
    prev = mk.clip(seq, image_prompt="a")
    img = mk.image()
    with owner_ctx.services.db.session() as s:
        s.get(Clip, prev).image_take_id = mk.take(prev, "image", img)
    cid = mk.clip(seq, image_prompt="b", link="continue")
    cp = compile_image(owner_ctx, cid)
    assert [r.media_id for r in cp.refs] == [img] and any("chưa có video" in w for w in cp.warnings)


def test_scene_location_and_state_used(owner_ctx, mk):
    loc, _loc_look = mk.asset("location", "Bến sông", "Bến đò gỗ")
    seq = mk.sequence()
    with owner_ctx.services.db.session() as s:
        sc = Scene(sequence_id=seq, key="s1", rank="a0", location_asset_id=loc, summary="Hoàng hôn ở bến",
                   state={"time_of_day": "dusk", "weather": "light rain", "lighting": "warm tungsten from left"})
        s.add(sc)
        s.flush()
        scene_id = sc.id
    cid = mk.clip(seq, scene_id=scene_id, image_prompt="p")
    text = compile_image(owner_ctx, cid).text
    assert "Bến sông: Bến đò gỗ" in text and "dusk, light rain." in text
    assert "Key light: warm tungsten from left." in text and "Hoàng hôn ở bến" in text


def test_look_with_too_many_refs_warns(owner_ctx, mk):
    char, look = mk.asset("character", "An", "d")
    out = library.look_add_refs(owner_ctx, look, [mk.image() for _ in range(7)])
    assert out["image_count"] == 7 and out["warnings"]
    cid = mk.clip(mk.sequence(), image_prompt="p", assets=[{"asset_id": char}])
    assert any("7 ảnh" in w for w in compile_image(owner_ctx, cid).warnings)
    with owner_ctx.services.db.session() as s:
        assert s.get(Asset, char).deleted_at is None


def test_speech_counts_syllables_not_chars():
    from sagareel.core import speech

    assert speech.syllables("Ba về rồi!") == 3
    assert speech.syllables("Xe đã chở gần hết nhà tôi—vậy mà bố vẫn chừa cho tôi một chỗ.") == 16
    assert speech.max_syllables(6) == 18 and speech.fits("Ba ơi, con đợi ba ở đây nè.", 6)
