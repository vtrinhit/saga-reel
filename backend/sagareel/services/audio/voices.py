"""Hồ sơ giọng (`voice_profile`) + chọn giọng cho một câu thoại (spec §11).

Thứ tự chọn giọng của câu (tầng sau thắng): cài đặt `tts.connection`/`tts.voice` → hồ sơ người kể của project
(`project.settings["narrator_voice_profile_id"]`, chỉ khi câu không có người nói) → hồ sơ của nhân vật nói
(`asset.voice_profile_id`) → `per_lang[lang]` của hồ sơ đó → `dialogue_line.voice_override`.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sagareel.core.context import OpContext
from sagareel.core.errors import ValidationFailed
from sagareel.core.resolver import Layer
from sagareel.db.models import Asset, Project, Sequence, VoiceProfile
from sagareel.services import entities
from sagareel.services.projects import slugify
from sagareel.services.settings import global_layer

RATE_RE = re.compile(r"^[+-]\d{1,3}%$")
PITCH_RE = re.compile(r"^[+-]\d{1,4}Hz$")
VOICE_FIELDS = ("connection", "voice", "rate", "pitch", "volume", "style")
_FORMATS = {"rate": (RATE_RE, "+0%"), "volume": (RATE_RE, "+0%"), "pitch": (PITCH_RE, "+0Hz")}


def _check_formats(values: dict[str, Any], prefix: str = "values") -> None:
    errors = []
    for k, (rx, example) in _FORMATS.items():
        v = values.get(k)
        if v is not None and (not isinstance(v, str) or not rx.match(v)):
            errors.append({"loc": [prefix, k], "msg": f"'{k}' phải có dạng {example} (vd. {example[0]}10{example[2:]})",
                           "type": "pattern"})
    per_lang = values.get("per_lang")
    if isinstance(per_lang, dict):
        for lang, cfg in per_lang.items():
            if not isinstance(cfg, dict):
                errors.append({"loc": [prefix, "per_lang", lang], "msg": "Mỗi ngôn ngữ là object "
                               "{connection?, voice?, rate?, pitch?, volume?}.", "type": "type_error"})
                continue
            for k, (rx, example) in _FORMATS.items():
                v = cfg.get(k)
                if v is not None and (not isinstance(v, str) or not rx.match(v)):
                    errors.append({"loc": [prefix, "per_lang", lang, k], "msg": f"'{k}' phải có dạng {example}",
                                   "type": "pattern"})
    if errors:
        raise ValidationFailed("Thông số giọng không hợp lệ.", hint="Xem `extra.errors`.", extra={"errors": errors})


def _free_key(s: Session, project_id: int | None, base: str) -> str:
    key, n = base, 1
    while s.scalar(select(func.count()).select_from(VoiceProfile).where(
            VoiceProfile.project_id.is_(None) if project_id is None else VoiceProfile.project_id == project_id,
            VoiceProfile.key == key)):
        n += 1
        key = f"{base}-{n}"
    return key


def create(ctx: OpContext, name: str, *, project_id: int | None = None, key: str | None = None,
           **values: Any) -> dict[str, Any]:
    if not name or not name.strip():
        raise ValidationFailed("Tên hồ sơ giọng không được để trống.")
    clean = {k: v for k, v in values.items() if v is not None}
    _check_formats(clean)
    u = entities.uow(ctx, f"Tạo hồ sơ giọng {name}", "voice.create")
    try:
        with u as s:
            if project_id is not None:
                entities.get(s, "project", project_id)
            u.project_id = project_id
            vp = VoiceProfile(project_id=project_id, name=name,
                              key=key or _free_key(s, project_id, slugify(name, "voice")), **clean)
            s.add(vp)
            s.flush()
            out = entities.to_dict(vp)
    except IntegrityError as e:
        raise entities.integrity_error("voice_profile", e) from None
    return out


def update(ctx: OpContext, id_: int, values: dict[str, Any], expected_updated_at: datetime | None = None) -> dict:
    _check_formats(values)
    return entities.patch(ctx, "voice_profile", id_, values, expected_updated_at)


def list_profiles(ctx: OpContext, project_id: int | None = None, include_global: bool = True) -> list[dict]:
    q = select(VoiceProfile).where(VoiceProfile.deleted_at.is_(None))
    if project_id is None:
        q = q.where(VoiceProfile.project_id.is_(None))
    elif include_global:
        q = q.where(or_(VoiceProfile.project_id == project_id, VoiceProfile.project_id.is_(None)))
    else:
        q = q.where(VoiceProfile.project_id == project_id)
    with ctx.services.db.session() as s:
        return [entities.to_dict(v) for v in s.scalars(q.order_by(VoiceProfile.project_id, VoiceProfile.id))]


def _impact(s: Session, rows: list[Any]) -> dict[str, Any]:
    ids = [r.id for r in rows]
    used = s.scalars(select(Asset).where(Asset.voice_profile_id.in_(ids), Asset.deleted_at.is_(None))).all()
    return {"used_by_assets": [{"id": a.id, "key": a.key, "name": a.name} for a in used]}


entities.IMPACT_EXTRA["voice_profile"] = _impact


# --- Chọn giọng -----------------------------------------------------------------------------------------------------


def setting(s: Session, services: Any, key: str, project: Project | None = None,
            sequence: Sequence | None = None) -> Any:
    """Giá trị hiệu lực của khoá cài đặt qua các tầng global → project → sequence."""
    layers = [global_layer(s)]
    if project is not None:
        layers.append(Layer("project", project.id, dict(project.settings or {})))
    if sequence is not None:
        layers.append(Layer("sequence", sequence.id, dict(sequence.settings or {})))
    return services.resolver.resolve(key, layers).value


def _apply(out: dict, cfg: dict | None) -> None:
    for k in VOICE_FIELDS:
        v = (cfg or {}).get(k)
        if v not in (None, ""):
            out[k] = v


def resolve(s: Session, services: Any, *, project: Project, sequence: Sequence | None = None,
            speaker_asset_id: int | None = None, voice_override: dict | None = None,
            lang: str | None = None) -> dict[str, Any]:
    """`{connection, voice, rate, pitch, volume, style, profile_id}` cho một câu thoại."""
    out: dict[str, Any] = {"connection": setting(s, services, "tts.connection", project, sequence),
                           "voice": setting(s, services, "tts.voice", project, sequence),
                           "rate": "+0%", "pitch": "+0Hz", "volume": "+0%", "style": "", "profile_id": None}
    profile_id = None
    if speaker_asset_id is not None:
        a = s.get(Asset, speaker_asset_id)
        if a is not None and a.deleted_at is None:
            profile_id = a.voice_profile_id
    else:
        profile_id = (project.settings or {}).get("narrator_voice_profile_id")
    if profile_id is not None:
        vp = s.get(VoiceProfile, int(profile_id))
        if vp is not None and vp.deleted_at is None:
            _apply(out, {k: getattr(vp, k) for k in VOICE_FIELDS})
            out["profile_id"] = vp.id
            if lang and isinstance((vp.per_lang or {}).get(lang), dict):
                _apply(out, vp.per_lang[lang])
    _apply(out, voice_override)
    return out
