"""Fixture Task L2: project "giống e2e" đủ mọi phần của file khai báo (preset, Library + look + media, giọng, âm thanh,
nhóm tập, 2 tập, cảnh, clip đủ trường shot, thoại, timeline nhạc/SFX)."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

from sagareel.core import rank
from sagareel.db.models import (
    Asset,
    AssetKind,
    AssetVariant,
    Clip,
    DialogueLine,
    GroupNode,
    Preset,
    Project,
    Scene,
    Sequence,
    Sound,
    TimelineItem,
    VoiceProfile,
)
from sagareel.services import media, projects


def tiny_png(path: Path, rgb: tuple[int, int, int]) -> Path:
    """PNG 2×2 một màu (không cần ffmpeg)."""
    raw = b"".join(b"\x00" + bytes(rgb) * 2 for _ in range(2))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    return path


def build_demo(ctx, tmp_path: Path, make_clip_tiny=None) -> int:
    """Tạo project "saga" đầy đủ; trả project_id."""
    services = ctx.services
    settings = services.settings
    pid = projects.create(ctx, name="Saga Đêm Khuya", key="saga", language="vi")["id"]
    imgs = []
    with services.db.session() as s:
        for i, rgb in enumerate([(200, 10, 10), (10, 200, 10), (10, 10, 200), (90, 90, 90)]):
            m = media.register(s, tiny_png(tmp_path / f"img{i}.png", rgb), kind="image", origin="fake",
                               settings=settings, project_id=pid)
            imgs.append(m.id)
    video_id = make_clip_tiny(services.db, pid) if make_clip_tiny else None

    u = ctx.uow("fixture saga", op_name="test.fixture")
    with u as s:
        u.project_id = pid
        proj = s.get(Project, pid)
        pack = Preset(project_id=pid, kind="style_pack", key="noir", name="Noir",
                      data={"style_block": "film noir, high contrast", "lut": "noir.cube"})
        s.add(pack)
        s.add(Preset(project_id=pid, kind="voice", key="calm", name="Giọng trầm", data={"rate": "-5%"}))
        s.flush()
        proj.style_pack_id = pack.id
        proj.style_block = "Ánh sáng lạnh, mưa đêm.\nMàu teal-orange."
        proj.negative_block = "chữ, logo"
        proj.target_platforms = ["tiktok", "youtube_shorts"]
        proj.settings = {"image.connection": "maivideo", "video.connection": "groktele", "image.model": "x1",
                         "subtitle.style": "bold"}
        proj.cover_media_id = imgs[3]

        s.add(AssetKind(project_id=pid, key="creature", label="Sinh vật", icon="paw", ref_priority=3))
        voice = VoiceProfile(project_id=pid, key="narrator", name="Người kể", connection="edge-tts",
                             voice="vi-VN-NamMinhNeural", rate="-5%", per_lang={"en": {"voice": "en-US-Guy"}})
        s.add(voice)
        s.flush()
        hero = Asset(project_id=pid, kind="character", key="hero", name="Lâm", description="Nam 25 tuổi, sẹo má trái. ",
                     locked=True, voice_profile_id=voice.id, tags=["chính"], custom={"age": 25}, cover_media_id=imgs[0])
        town = Asset(project_id=pid, kind="location", key="town", name="Phố cổ", description="Phố cổ mưa đêm")
        cat = Asset(project_id=pid, kind="creature", key="cat", name="Mèo đen")
        s.add_all([hero, town, cat])
        s.flush()
        lk1 = AssetVariant(asset_id=hero.id, key="default", name="Mặc định", lock_word="LAMHERO",
                           keep_features=["sẹo má trái", "tóc ngắn"], anchor_media_id=imgs[0],
                           sheet={"front": imgs[0], "side": imgs[1]}, ref_media_ids=[imgs[1]], is_default=True)
        lk2 = AssetVariant(asset_id=hero.id, key="wounded", name="Bị thương", description_delta="băng trán",
                           lock_word="LAMWOUND", keep_features=["sẹo má trái"], valid_from=2, valid_to=5)
        s.add_all([lk1, lk2])
        snd_music = Sound(project_id=pid, media_id=video_id or imgs[2], kind="music", title="Mưa", tags=["buồn"],
                          license="CC0")
        snd_sfx = Sound(project_id=pid, media_id=imgs[2], kind="sfx", title="Sấm")
        s.add_all([snd_music, snd_sfx])
        g1 = GroupNode(project_id=pid, kind="series", key="s1", title="Mùa 1", rank=rank.between(None, None))
        s.add(g1)
        s.flush()
        g2 = GroupNode(project_id=pid, kind="arc", key="arc1", title="Hồi đầu", parent_id=g1.id,
                       rank=rank.between(g1.rank, None), synopsis="Mở đầu")
        s.add(g2)
        s.flush()

        for n in (1, 2):
            q = Sequence(project_id=pid, key=f"ep{n}", number=n, title=f"Tập {n}", group_id=g2.id,
                         synopsis="Lâm về phố cổ.", script="CẢNH 1\nLâm bước đi.", workflow_key="series",
                         gates={"after_images": True}, target_platforms=["tiktok"], settings={"video.engine": "ai"},
                         status="done", pipeline={"step": "x"})
            s.add(q)
            s.flush()
            sc_ranks = rank.n_after(None, 2)
            sc1 = Scene(sequence_id=q.id, key="sc1", rank=sc_ranks[0], title="Phố", location_asset_id=town.id,
                        summary="Mưa", state={"time_of_day": "night"})
            sc2 = Scene(sequence_id=q.id, key="sc2", rank=sc_ranks[1], title="Nhà")
            s.add_all([sc1, sc2])
            s.flush()
            cranks = rank.n_after(None, 3)
            clips = []
            for i in range(3):
                c = Clip(
                    sequence_id=q.id, key=f"c{i + 1:03d}", rank=cranks[i], scene_id=(sc1 if i < 2 else sc2).id,
                    title=f"Clip {i + 1}",
                    image_prompt=f"LAMHERO đứng dưới mưa, cảnh {i + 1} \nDòng hai có dấu cách cuối ",
                    motion_prompt="Máy đẩy chậm vào mặt.", prompt_mode="verbatim" if i == 0 else "synced",
                    assets=[{"asset_id": hero.id, "variant_id": lk1.id, "role": "main"}, {"asset_id": town.id}],
                    extra_ref_media_ids=[imgs[1]], input_media_id=imgs[2] if i == 2 else None,
                    engine="still_motion" if i == 2 else "ai_video", motion_preset="ken_burns" if i == 2 else "",
                    duration_ms=6000 + i * 1000, link="fade" if i == 1 else "cut",
                    transition={"kind": "xfade", "duration": 0.5} if i == 1 else {}, trim_in_ms=100 * i,
                    speed=1.25 if i == 0 else 1.0, grok_audio="mute", shot_size="cu", angle="low",
                    camera_move="push_in", lens="35mm", composition="thirds_l", eyeline="screen_left",
                    screen_dir="L→R", first_frame_desc="mặt", last_frame_desc="mắt", sfx_desc="mưa rơi",
                    beats=[{"t": 0, "text": "nhìn"}], custom={"axis_side": "A", "cut_in": "match_action"},
                    settings={"image.model": "x2"}, review="approved", flags={"stale_image": True}, notes="ghi chú",
                )
                s.add(c)
                clips.append(c)
            s.flush()
            dr = rank.n_after(None, 2)
            s.add(DialogueLine(clip_id=clips[0].id, rank=dr[0], speaker_asset_id=hero.id, text="Mưa rồi…",
                               emotion="buồn", translations={"en": "It's raining…"}))
            s.add(DialogueLine(clip_id=clips[0].id, rank=dr[1], speaker_asset_id=None, text="Người kể: đêm ấy.",
                               offset_ms=2500, subtitle=False))
            s.add(DialogueLine(clip_id=clips[1].id, rank=dr[0], speaker_asset_id=hero.id, text="Đi thôi.",
                               voice_override={"rate": "+10%"}))
            s.add(TimelineItem(sequence_id=q.id, track="music", anchor_clip_id=clips[0].id,
                               end_anchor={"sequence_end": True}, sound_id=snd_music.id, media_id=snd_music.media_id,
                               params={"volume": 0.4, "duck": True}))
            s.add(TimelineItem(sequence_id=q.id, track="sfx", anchor_clip_id=clips[1].id, offset_ms=500,
                               duration_ms=1200, sound_id=snd_sfx.id, media_id=imgs[2],
                               end_anchor=None))
            s.add(TimelineItem(sequence_id=q.id, track="sfx", anchor_clip_id=clips[1].id, offset_ms=100,
                               end_anchor={"clip_id": clips[2].id}, sound_id=snd_sfx.id))
            s.add(TimelineItem(sequence_id=q.id, track="title", anchor_clip_id=None, duration_ms=2000,
                               params={"text": "Tập " + str(n)}))
    return pid
