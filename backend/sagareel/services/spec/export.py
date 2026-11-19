"""DB → file khai báo project (YAML). Hàm thuần trên `Session`; dùng chung cho xuất và cho kế hoạch (trạng thái hiện tại).

Thứ tự ổn định: preset/loại tài sản/giọng/tài sản/look/âm thanh theo id; nhóm, cảnh, clip, lời thoại theo `rank`; tập
theo (`number`, id); mục timeline theo (track, clip neo, offset_ms, id). Nhờ vậy xuất → áp vào DB rỗng → xuất lại cho
cùng một văn bản.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from sagareel.db.models import (
    Asset,
    AssetKind,
    AssetVariant,
    Clip,
    DialogueLine,
    GroupNode,
    Media,
    Preset,
    Project,
    Scene,
    Sequence,
    Sound,
    TimelineItem,
    VoiceProfile,
)
from sagareel.services.spec import models as m

PROVIDER_SUFFIX = ".connection"


def media_key(sha256: str) -> str:
    return f"m-{sha256[:12]}"


def split_settings(settings: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """`{"image.connection": "x", "k": v}` → (`{"k": v}`, `{"image": "x"}`)."""
    rest: dict[str, Any] = {}
    providers: dict[str, str] = {}
    for k, v in (settings or {}).items():
        if k.endswith(PROVIDER_SUFFIX) and isinstance(v, str):
            providers[k[: -len(PROVIDER_SUFFIX)]] = v
        else:
            rest[k] = v
    return rest, providers


def join_settings(settings: dict[str, Any], providers: dict[str, str]) -> dict[str, Any]:
    out = dict(settings)
    for kind, name in providers.items():
        out[f"{kind}{PROVIDER_SUFFIX}"] = name
    return out


def _plain(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    return v


def sound_keys(rows: Iterable[tuple[int, str, str]]) -> dict[int, str]:
    """Khoá suy ra của âm thanh: `(id, kind, sha256)` theo thứ tự → `<kind>-<sha[:10]>` (+ `-2`, `-3` khi trùng)."""
    out: dict[int, str] = {}
    seen: dict[str, int] = {}
    for id_, kind, sha in rows:
        base = f"{kind}-{(sha or 'none')[:10]}"
        seen[base] = seen.get(base, 0) + 1
        out[id_] = base if seen[base] == 1 else f"{base}-{seen[base]}"
    return out


def dialogue_key(clip_key: str, n: int) -> str:
    return f"{clip_key}#{n}"


def timeline_key(track: str, anchor: str | None, n: int) -> str:
    return f"{track}@{anchor or '-'}#{n}"


class _Media:
    """Gom media được tham chiếu: id → khoá trong `media`."""

    def __init__(self, s: Session) -> None:
        self.s = s
        self.entries: dict[str, m.MediaEntry] = {}
        self._by_id: dict[int, str | None] = {}

    def ref(self, media_id: Any) -> str | None:
        if not isinstance(media_id, int) or isinstance(media_id, bool):
            return None
        if media_id not in self._by_id:
            row = self.s.get(Media, media_id)
            if row is None:
                self._by_id[media_id] = None
            else:
                k = media_key(row.sha256)
                self.entries.setdefault(k, m.MediaEntry(
                    sha256=row.sha256, path=row.path, kind=row.kind, mime=row.mime or "", size=row.size or 0,
                    width=row.width, height=row.height, duration_ms=row.duration_ms,
                ))
                self._by_id[media_id] = k
        return self._by_id[media_id]

    def refs(self, ids: Iterable[Any]) -> list[str]:
        return [r for r in (self.ref(i) for i in ids or []) if r is not None]


def _alive(s: Session, model, *where) -> list[Any]:
    return list(s.scalars(select(model).where(model.deleted_at.is_(None), *where).order_by(model.id)))


def build(s: Session, project: Project, include: Iterable[str] | None = None) -> m.ProjectSpec:
    """Trạng thái project hiện tại dưới dạng `ProjectSpec` (đủ trường)."""
    sections = set(include) if include is not None else set(m.SECTIONS)
    med = _Media(s)

    presets_all = _alive(s, Preset, Preset.project_id == project.id)
    style_pack = None
    if project.style_pack_id is not None:
        row = s.get(Preset, project.style_pack_id)
        style_pack = row.key if row is not None else None
    settings, providers = split_settings(_plain(project.settings))
    block = m.ProjectBlock(
        key=project.key, name=project.name, description=project.description or "", genre_key=project.genre_key,
        style_pack=style_pack, aspect=project.aspect, resolution=project.resolution, fps=project.fps,
        language=project.language, languages=_plain(project.languages), target_platforms=_plain(project.target_platforms),
        style_block=project.style_block or "", negative_block=project.negative_block or "", settings=settings,
        budget=_plain(project.budget), cover=med.ref(project.cover_media_id),
    )

    assets = _alive(s, Asset, Asset.project_id == project.id)
    asset_key = {a.id: a.key for a in assets}
    # Tham chiếu tới tài sản đã xoá/khác project vẫn giữ được key nếu có dòng.
    look_key: dict[int, str] = {}
    looks_of: dict[int, list[AssetVariant]] = {}
    if assets:
        for lk in _alive(s, AssetVariant, AssetVariant.asset_id.in_([a.id for a in assets])):
            looks_of.setdefault(lk.asset_id, []).append(lk)
            look_key[lk.id] = lk.key
    voices = _alive(s, VoiceProfile, VoiceProfile.project_id == project.id)

    def asset_ref(aid: Any) -> str | None:
        if aid is None:
            return None
        if aid in asset_key:
            return asset_key[aid]
        row = s.get(Asset, aid)
        return row.key if row is not None else None

    def voice_ref(vid: Any) -> str | None:
        if vid is None:
            return None
        row = s.get(VoiceProfile, vid)
        return row.key if row is not None else None

    sounds = _alive(s, Sound, Sound.project_id == project.id)
    sound_sha = {x.id: (s.get(Media, x.media_id).sha256 if s.get(Media, x.media_id) is not None else "")
                 for x in sounds}
    skeys = sound_keys((x.id, x.kind, sound_sha[x.id]) for x in sounds)

    spec = m.ProjectSpec(spec_version=m.SPEC_VERSION, project=block, providers=providers)

    if "presets" in sections:
        spec.presets = [m.PresetSpec(kind=p.kind, key=p.key, name=p.name, data=_plain(p.data)) for p in presets_all]

    if "library" in sections:
        lib = m.LibrarySpec()
        lib.asset_kinds = [m.AssetKindSpec(key=k.key, label=k.label, icon=k.icon or "", ref_priority=k.ref_priority)
                           for k in _alive(s, AssetKind, AssetKind.project_id == project.id)]
        lib.voices = [m.VoiceSpec(
            key=v.key, name=v.name, connection=v.connection or "", voice=v.voice or "", rate=v.rate, pitch=v.pitch,
            volume=v.volume, style=v.style or "", per_lang=_plain(v.per_lang), sample=med.ref(v.sample_media_id),
        ) for v in voices]
        for a in assets:
            looks = [m.LookSpec(
                key=lk.key, name=lk.name, description_delta=lk.description_delta or "",
                keep_features=_plain(lk.keep_features), lock_word=lk.lock_word or "",
                anchor=med.ref(lk.anchor_media_id),
                sheet={view: r for view, mid in (lk.sheet or {}).items() if (r := med.ref(mid)) is not None},
                refs=med.refs(lk.ref_media_ids), valid_from=lk.valid_from, valid_to=lk.valid_to,
                is_default=bool(lk.is_default),
            ) for lk in looks_of.get(a.id, [])]
            lib.assets.append(m.AssetSpec(
                key=a.key, kind=a.kind, name=a.name, description=a.description or "", locked=bool(a.locked),
                voice=voice_ref(a.voice_profile_id), tags=_plain(a.tags), custom=_plain(a.custom),
                cover=med.ref(a.cover_media_id), looks=looks,
            ))
        lib.sounds = [m.SoundSpec(
            key=skeys[x.id], kind=x.kind, title=x.title, media=med.ref(x.media_id) or "", tags=_plain(x.tags),
            source=x.source or "", license=x.license or "", attribution=x.attribution or "",
        ) for x in sounds]
        spec.library = lib

    groups = list(s.scalars(select(GroupNode).where(GroupNode.project_id == project.id, GroupNode.deleted_at.is_(None))
                            .order_by(GroupNode.rank, GroupNode.id)))
    group_key = {g.id: g.key for g in groups}

    def group_ref(gid: Any) -> str | None:
        if gid is None:
            return None
        if gid in group_key:
            return group_key[gid]
        row = s.get(GroupNode, gid)
        return row.key if row is not None else None

    if "groups" in sections:
        spec.groups = [m.GroupSpec(key=g.key, kind=g.kind, title=g.title, parent=group_ref(g.parent_id),
                                   synopsis=g.synopsis or "") for g in groups]

    if "sequences" in sections:
        spec.sequences = []
        seqs = s.scalars(select(Sequence).where(Sequence.project_id == project.id, Sequence.deleted_at.is_(None))
                         .order_by(Sequence.number, Sequence.id)).all()
        for q in seqs:
            spec.sequences.append(_sequence(s, q, med, asset_ref, look_key, group_ref, skeys))

    spec.media = dict(sorted(med.entries.items()))
    return spec


def _sequence(s: Session, q: Sequence, med: _Media, asset_ref, look_key: dict[int, str], group_ref,
              skeys: dict[int, str]) -> m.SequenceSpec:
    scenes = s.scalars(select(Scene).where(Scene.sequence_id == q.id, Scene.deleted_at.is_(None))
                       .order_by(Scene.rank, Scene.id)).all()
    scene_key = {x.id: x.key for x in scenes}
    clips = s.scalars(select(Clip).where(Clip.sequence_id == q.id, Clip.deleted_at.is_(None))
                      .order_by(Clip.rank, Clip.id)).all()
    clip_key = {c.id: c.key for c in clips}
    clip_pos = {c.id: i for i, c in enumerate(clips)}

    def clip_ref(cid: Any) -> str | None:
        if cid is None:
            return None
        if cid in clip_key:
            return clip_key[cid]
        row = s.get(Clip, cid)
        return row.key if row is not None else None

    def scene_ref(sid: Any) -> str | None:
        if sid is None:
            return None
        if sid in scene_key:
            return scene_key[sid]
        row = s.get(Scene, sid)
        return row.key if row is not None else None

    lines_of: dict[int, list[DialogueLine]] = {}
    if clips:
        for ln in s.scalars(select(DialogueLine).where(DialogueLine.clip_id.in_(list(clip_key)),
                                                       DialogueLine.deleted_at.is_(None))
                            .order_by(DialogueLine.rank, DialogueLine.id)):
            lines_of.setdefault(ln.clip_id, []).append(ln)

    def look_ref(vid: Any) -> str | None:
        if vid is None:
            return None
        if vid in look_key:
            return look_key[vid]
        row = s.get(AssetVariant, vid)
        return row.key if row is not None else None

    def clip_assets(items: list[Any]) -> list[m.ClipAssetRef]:
        out = []
        for it in items or []:
            if not isinstance(it, dict):
                continue
            extra = {k: _plain(v) for k, v in it.items() if k not in ("asset_id", "variant_id", "role")}
            key = asset_ref(it.get("asset_id"))
            if key is None:
                continue
            out.append(m.ClipAssetRef(asset=key, look=look_ref(it.get("variant_id")), role=it.get("role"), **extra))
        return out

    clip_specs = []
    for c in clips:
        clip_specs.append(m.ClipSpec(
            key=c.key, scene=scene_ref(c.scene_id), title=c.title or "", image_prompt=c.image_prompt or "",
            image_prompt_src=_plain(c.image_prompt_src), motion_prompt=c.motion_prompt or "",
            motion_prompt_src=_plain(c.motion_prompt_src), prompt_mode=c.prompt_mode, assets=clip_assets(c.assets),
            extra_refs=med.refs(c.extra_ref_media_ids), input=med.ref(c.input_media_id), engine=c.engine,
            motion_preset=c.motion_preset or "", duration_ms=c.duration_ms, link=c.link,
            transition=_plain(c.transition), trim_in_ms=c.trim_in_ms, trim_out_ms=c.trim_out_ms, speed=c.speed,
            grok_audio=c.grok_audio, subtitle_mode=c.subtitle_mode, notes=c.notes or "", shot_size=c.shot_size or "",
            angle=c.angle or "", camera_move=c.camera_move or "", lens=c.lens or "", composition=c.composition or "",
            eyeline=c.eyeline or "", screen_dir=c.screen_dir or "", first_frame_desc=c.first_frame_desc or "",
            last_frame_desc=c.last_frame_desc or "", sfx_desc=c.sfx_desc or "", beats=_plain(c.beats),
            custom=_plain(c.custom), settings=_plain(c.settings),
            dialogue=[m.DialogueSpec(
                speaker=asset_ref(ln.speaker_asset_id), text=ln.text, emotion=ln.emotion or "", source=ln.source,
                voice_override=_plain(ln.voice_override), offset_ms=ln.offset_ms, subtitle=bool(ln.subtitle),
                translations=_plain(ln.translations),
            ) for ln in lines_of.get(c.id, [])],
        ))

    items = s.scalars(select(TimelineItem).where(TimelineItem.sequence_id == q.id,
                                                 TimelineItem.deleted_at.is_(None))).all()
    items = sorted(items, key=lambda t: (t.track, clip_pos.get(t.anchor_clip_id, -1), t.offset_ms, t.id))
    timeline = []
    for t in items:
        end = t.end_anchor
        if isinstance(end, dict) and "clip_id" in end:
            end = {"clip": clip_ref(end.get("clip_id")),
                   **{k: _plain(v) for k, v in end.items() if k != "clip_id"}}
        else:
            end = _plain(end)
        timeline.append(m.TimelineSpec(
            track=t.track, anchor=clip_ref(t.anchor_clip_id), offset_ms=t.offset_ms, end_anchor=end,
            duration_ms=t.duration_ms, media=med.ref(t.media_id), sound=skeys.get(t.sound_id),
            params=_plain(t.params),
        ))

    return m.SequenceSpec(
        key=q.key, number=q.number, title=q.title, group=group_ref(q.group_id), synopsis=q.synopsis or "",
        script=q.script or "", workflow_key=q.workflow_key or "", gates=_plain(q.gates),
        target_platforms=_plain(q.target_platforms), settings=_plain(q.settings),
        scenes=[m.SceneSpec(key=x.key, title=x.title or "", location=asset_ref(x.location_asset_id),
                            summary=x.summary or "", state=_plain(x.state), settings=_plain(x.settings))
                for x in scenes],
        clips=clip_specs, timeline=timeline,
    )


# --- YAML -----------------------------------------------------------------------------------------------------------


class _Dumper(yaml.SafeDumper):
    pass


def _str(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    # Chuỗi nhiều dòng → khối `|` (PyYAML tự quay về dạng có ngoặc nếu khối không giữ nguyên được, vd. dấu cách cuối
    # dòng) → luôn đọc lại đúng từng ký tự.
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_Dumper.add_representer(str, _str)

HEADER = ("# SagaReel project spec — sửa rồi chạy project.plan → project.apply (khoá ổn định, nhập lại không tạo "
          "trùng).\n# Schema: docs/schema/project-spec.schema.json\n")


def to_data(spec: m.ProjectSpec) -> dict[str, Any]:
    return spec.model_dump(mode="json", exclude_defaults=True, exclude_none=False)


def to_yaml(spec: m.ProjectSpec) -> str:
    body = yaml.dump(to_data(spec), Dumper=_Dumper, allow_unicode=True, sort_keys=False, width=1 << 30,
                     default_flow_style=False)
    return HEADER + body
