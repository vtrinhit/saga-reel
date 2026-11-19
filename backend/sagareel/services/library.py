"""Library (spec §6, §8, §10, §23.9; phụ lục film-craft §1): loại tài sản, tài sản, look, sheet, drift check.

- Mọi ghi thực thể ★ đi qua UnitOfWork (lịch sử, hoàn tác). Take là bảng Plain: ghi thẳng, không bao giờ xoá/ghi đè.
- Sheet: mỗi (look, góc, take) là **một job** `library.sheet` (một lần gọi provider có tiền mỗi job — ngân sách và
  việc nối lại theo `external_id` đều tính theo job). Góc `anchor` sinh chỉ từ chữ; mọi góc khác chỉ ref ảnh mỏ neo.
  Look chưa có mỏ neo: chỉ gửi job anchor, job anchor đầu tiên thành công đặt `anchor_media_id` rồi mới gửi các góc
  còn lại (danh sách mang trong `params.follow`).
- Drift check / chấm take: một job LLM vision mỗi take (`library.drift_check`, `library.score_take`).
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sagareel.compile import compiler
from sagareel.core.context import OpContext
from sagareel.core.errors import AppError, Conflict, NotFound, ValidationFailed
from sagareel.core.uow import UnitOfWork
from sagareel.db.base import utcnow
from sagareel.db.models import (
    Asset,
    AssetKind,
    AssetVariant,
    Clip,
    Media,
    Project,
    ProviderConnection,
    Sequence,
    Take,
)
from sagareel.services import budget, entities, jobs, llm, media
from sagareel.services.projects import KEY_RE, slugify

SHEET_JOB = "library.sheet"
DRIFT_JOB = "library.drift_check"
SCORE_JOB = "library.score_take"
SYSTEM_ACTOR = "system:library"
DRIFT_CHECKLIST = ("face", "hair", "outfit", "colors", "props", "hands", "stray text or watermark")
MAX_TAKES = 8
MAX_IDS = 500


# --- Tiện ích -----------------------------------------------------------------------------------------------------


def _invalid(msg: str, loc: str) -> ValidationFailed:
    return ValidationFailed(msg, extra={"errors": [{"loc": [loc], "msg": msg, "type": "value_error"}]})


def _check_name(name: str, what: str = "Tên") -> str:
    if not isinstance(name, str) or not name.strip():
        raise _invalid(f"{what} không được để trống.", "name")
    return name


def _check_key(key: str) -> None:
    if not KEY_RE.match(key or ""):
        raise _invalid("Mã chỉ gồm chữ không dấu, số, '-' hoặc '_' (tối đa 64 ký tự).", "key")


def _fold(text: str) -> str:
    text = text.replace("đ", "d").replace("Đ", "D")
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def initials_key(name: str) -> str:
    """"An" → "AN", "Bà Tư" → "BT", "Căn nhà cũ" → "CNC" (mã ngắn kiểu kịch bản)."""
    words = re.findall(r"[A-Za-z0-9]+", _fold(name))
    if not words:
        return "A"
    if len(words) == 1:
        return words[0][:4].upper()
    return "".join(w[0] for w in words)[:6].upper()


def _free(existing: set[str], base: str) -> str:
    if base not in existing:
        return base
    n = 2
    while f"{base}{n}" in existing:
        n += 1
    return f"{base}{n}"


def _uow(ctx: OpContext, summary: str, default_op: str, project_id: int | None) -> UnitOfWork:
    u = entities.uow(ctx, summary, default_op)
    u.project_id = project_id
    return u


def _set(s: Session, type_: str, obj: Any, values: dict[str, Any], expected_updated_at: datetime | None) -> None:
    clean = entities.check_values(type_, values)
    entities.check_fresh(type_, obj, expected_updated_at)
    for k, v in clean.items():
        setattr(obj, k, v)


def take_dict(t: Take) -> dict[str, Any]:
    return {
        "id": t.id, "owner_type": t.owner_type, "owner_id": t.owner_id, "part": t.part, "view": t.view,
        "media_id": t.media_id, "status": t.status, "job_id": t.job_id, "inputs": dict(t.inputs or {}),
        "parent_take_id": t.parent_take_id, "cost": t.cost, "elapsed_ms": t.elapsed_ms, "rating": t.rating,
        "verdict": t.verdict, "notes": t.notes, "auto_score": dict(t.auto_score or {}), "lang": t.lang,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }


def _media_of_kind(s: Session, media_id: int, kind: str = "image") -> Media:
    m = media.get(s, media_id)
    if m.kind != kind:
        raise AppError("media_kind_mismatch", f"Media #{media_id} là {m.kind}, cần {kind}.", status=422)
    return m


def _get_project(s: Session, project_id: int) -> Project:
    return entities.get(s, "project", project_id)


# --- Loại tài sản ---------------------------------------------------------------------------------------------------


def kind_keys(s: Session, project_id: int | None) -> set[str]:
    q = select(AssetKind.key).where(AssetKind.deleted_at.is_(None))
    q = q.where(AssetKind.project_id.is_(None)) if project_id is None else q.where(
        or_(AssetKind.project_id.is_(None), AssetKind.project_id == project_id))
    return set(s.scalars(q).all())


def asset_kind_list(ctx: OpContext, project_id: int | None = None) -> dict[str, Any]:
    with ctx.services.db.session() as s:
        q = select(AssetKind).where(AssetKind.deleted_at.is_(None))
        q = q.where(AssetKind.project_id.is_(None)) if project_id is None else q.where(
            or_(AssetKind.project_id.is_(None), AssetKind.project_id == project_id))
        rows = s.scalars(q.order_by(AssetKind.ref_priority.desc(), AssetKind.id)).all()
        items = []
        for r in rows:
            d = entities.to_dict(r)
            info = compiler.kind_info(r.key)
            d["views"] = list(info["views"])
            d["default_views"] = list(info["default_views"])
            items.append(d)
        return {"items": items}


def asset_kind_create(ctx: OpContext, key: str, label: str, project_id: int | None = None, icon: str = "",
                      ref_priority: int = 0) -> dict[str, Any]:
    if not re.match(r"^[a-z][a-z0-9_]{0,31}$", key or ""):
        raise _invalid("Mã loại chỉ gồm chữ thường không dấu, số, '_' và bắt đầu bằng chữ.", "key")
    _check_name(label, "Nhãn")
    with _uow(ctx, f"Thêm loại tài sản {key}", "asset_kind.create", project_id) as s:
        if project_id is not None:
            _get_project(s, project_id)
        taken = s.scalars(select(AssetKind).where(AssetKind.key == key, or_(
            AssetKind.project_id.is_(None), AssetKind.project_id == project_id))).first()
        if taken is not None:
            raise Conflict(f"Đã có loại tài sản '{key}'.", code="key_taken", hint="Chọn mã khác.")
        row = AssetKind(project_id=project_id, key=key, label=label, icon=icon, ref_priority=ref_priority)
        s.add(row)
        s.flush()
        return entities.to_dict(row)


def asset_kind_update(ctx: OpContext, kind_id: int, values: dict[str, Any],
                      expected_updated_at: datetime | None = None) -> dict[str, Any]:
    if "key" in values:
        raise AppError("readonly_field", "Không đổi được mã của loại tài sản (tài sản đang tham chiếu theo mã).",
                       status=422, extra={"fields": ["key"]})
    return entities.patch(ctx, "asset_kind", kind_id, values, expected_updated_at)


# --- Tài sản ----------------------------------------------------------------------------------------------------


def _looks(s: Session, asset_id: int) -> list[AssetVariant]:
    return compiler.looks_of(s, asset_id)


def look_out(s: Session, lk: AssetVariant, *, limit: int | None = None) -> dict[str, Any]:
    d = entities.to_dict(lk)
    n = len(compiler.look_images(lk))
    limit = limit if limit is not None else 6
    d["image_count"] = n
    d["warnings"] = [f"Look có {n} ảnh tham chiếu (> {limit}): nên giữ bộ ref tối giản."] if n > limit else []
    return d


def asset_out(s: Session, a: Asset) -> dict[str, Any]:
    d = entities.to_dict(a)
    limit = int(compiler.setting(s, "library.look_max_refs", s.get(Project, a.project_id)))
    d["looks"] = [look_out(s, lk, limit=limit) for lk in _looks(s, a.id)]
    return d


def asset_create(ctx: OpContext, project_id: int, kind: str, name: str, key: str | None = None,
                 description: str = "", tags: list[str] | None = None) -> dict[str, Any]:
    """Tạo tài sản + một look mặc định ("Mặc định", is_default) để gắn mỏ neo/sheet."""
    _check_name(name)
    if key is not None:
        _check_key(key)
    u = _uow(ctx, f"Thêm tài sản {name}", "asset.create", project_id)
    try:
        with u as s:
            _get_project(s, project_id)
            if kind not in kind_keys(s, project_id):
                raise AppError("asset_kind_unknown", f"Không có loại tài sản '{kind}'.",
                               hint="Gọi asset_kind.list để xem các loại; asset_kind.create để thêm.", status=422)
            existing = set(s.scalars(select(Asset.key).where(Asset.project_id == project_id)).all())
            if key is not None and key in existing:
                raise Conflict(f"Mã '{key}' đã được dùng trong dự án.", code="key_taken", hint="Chọn mã khác.")
            a = Asset(project_id=project_id, kind=kind, key=key or _free(existing, initials_key(name)), name=name,
                      description=description, tags=list(tags or []))
            s.add(a)
            s.flush()
            s.add(AssetVariant(asset_id=a.id, key="default", name="Mặc định", is_default=True))
            s.flush()
            return asset_out(s, a)
    except IntegrityError as e:
        raise entities.integrity_error("asset", e) from None


def asset_get(ctx: OpContext, asset_id: int) -> dict[str, Any]:
    with ctx.services.db.session() as s:
        return asset_out(s, entities.get(s, "asset", asset_id))


def asset_list(ctx: OpContext, project_id: int, kind: str | None = None, q: str | None = None,
               cursor: int | None = None, limit: int = 100) -> dict[str, Any]:
    limit = max(1, min(int(limit), entities.MAX_LIMIT))
    with ctx.services.db.session() as s:
        stmt = select(Asset).where(Asset.project_id == project_id, Asset.deleted_at.is_(None))
        if kind:
            stmt = stmt.where(Asset.kind == kind)
        if q:
            like = f"%{q}%"
            stmt = stmt.where(or_(Asset.name.ilike(like), Asset.key.ilike(like), Asset.description.ilike(like)))
        if cursor is not None:
            stmt = stmt.where(Asset.id > cursor)
        rows = s.scalars(stmt.order_by(Asset.id).limit(limit + 1)).all()
        more = len(rows) > limit
        rows = rows[:limit]
        return {"items": [asset_out(s, a) for a in rows], "next_cursor": rows[-1].id if more else None}


def asset_update(ctx: OpContext, asset_id: int, values: dict[str, Any],
                 expected_updated_at: datetime | None = None) -> dict[str, Any]:
    if "name" in values:
        _check_name(values["name"])
    if "key" in values:
        _check_key(values["key"])
    u = entities.uow(ctx, f"Sửa tài sản #{asset_id}", "asset.update")
    try:
        with u as s:
            a = entities.get(s, "asset", asset_id)
            u.project_id = a.project_id
            if "kind" in values and values["kind"] not in kind_keys(s, a.project_id):
                raise AppError("asset_kind_unknown", f"Không có loại tài sản '{values['kind']}'.", status=422)
            _set(s, "asset", a, values, expected_updated_at)
            s.flush()
            return asset_out(s, a)
    except IntegrityError as e:
        raise entities.integrity_error("asset", e) from None


def _clips_using(s: Session, project_id: int, asset_ids: set[int], look_ids: set[int] | None = None) -> list[int]:
    rows = s.execute(select(Clip.id, Clip.assets).join(Sequence, Sequence.id == Clip.sequence_id).where(
        Sequence.project_id == project_id, Clip.deleted_at.is_(None))).all()
    out = []
    for cid, items in rows:
        for it in items or []:
            if not isinstance(it, dict):
                continue
            if look_ids is not None:
                if it.get("variant_id") in look_ids:
                    out.append(cid)
                    break
            elif it.get("asset_id") in asset_ids:
                out.append(cid)
                break
    return out


def _asset_impact(s: Session, rows: list[Any]) -> dict[str, Any]:
    ids = {r.id for r in rows}
    looks = s.scalar(select(func.count()).select_from(AssetVariant).where(
        AssetVariant.asset_id.in_(ids), AssetVariant.deleted_at.is_(None)))
    clips = _clips_using(s, rows[0].project_id, ids) if rows else []
    return {"looks": looks, "clips_using": len(clips), "clip_ids": clips[:50]}


def _look_impact(s: Session, rows: list[Any]) -> dict[str, Any]:
    if not rows:
        return {}
    a = s.get(Asset, rows[0].asset_id)
    clips = _clips_using(s, a.project_id, set(), {r.id for r in rows})
    takes = s.scalar(select(func.count()).select_from(Take).where(
        Take.owner_type == "variant", Take.owner_id.in_([r.id for r in rows])))
    return {"asset": {"id": a.id, "key": a.key, "name": a.name}, "clips_using": len(clips),
            "clip_ids": clips[:50], "sheet_takes": takes}


entities.IMPACT_EXTRA["asset"] = _asset_impact
entities.IMPACT_EXTRA["asset_variant"] = _look_impact


def asset_delete_preview(ctx: OpContext, asset_id: int) -> dict[str, Any]:
    return entities.soft_delete_preview(ctx, "asset", [asset_id])


def asset_delete(ctx: OpContext, confirm_token: str | None) -> dict[str, Any]:
    return entities.soft_delete(ctx, confirm_token, expect_type="asset")


def _clone(obj: Any, **overrides: Any) -> Any:
    from sagareel.services.projects import _clone as clone

    return clone(obj, **overrides)


def asset_duplicate(ctx: OpContext, asset_id: int, name: str | None = None, key: str | None = None
                    ) -> dict[str, Any]:
    """Bản sao tài sản + mọi look (ảnh mỏ neo/sheet dùng chung media, không chép take)."""
    if name is not None:
        _check_name(name)
    if key is not None:
        _check_key(key)
    u = entities.uow(ctx, f"Nhân bản tài sản #{asset_id}", "asset.duplicate")
    try:
        with u as s:
            src = entities.get(s, "asset", asset_id)
            u.project_id = src.project_id
            existing = set(s.scalars(select(Asset.key).where(Asset.project_id == src.project_id)).all())
            if key is not None and key in existing:
                raise Conflict(f"Mã '{key}' đã được dùng trong dự án.", code="key_taken", hint="Chọn mã khác.")
            new_name = name or f"{src.name} (bản sao)"
            c = _clone(src, key=key or _free(existing, src.key), name=new_name)
            s.add(c)
            s.flush()
            for lk in _looks(s, src.id):
                s.add(_clone(lk, asset_id=c.id))
            s.flush()
            return asset_out(s, c)
    except IntegrityError as e:
        raise entities.integrity_error("asset", e) from None


# --- Look -------------------------------------------------------------------------------------------------------


def _check_range(valid_from: Any, valid_to: Any) -> None:
    if isinstance(valid_from, int) and isinstance(valid_to, int) and valid_from > valid_to:
        raise _invalid("valid_from phải ≤ valid_to (số tập).", "valid_from")


def _unset_other_defaults(s: Session, look: AssetVariant) -> None:
    for other in _looks(s, look.asset_id):
        if other.id != look.id and other.is_default:
            other.is_default = False


def look_create(ctx: OpContext, asset_id: int, name: str, description_delta: str = "",
                keep_features: list[str] | None = None, lock_word: str = "", valid_from: int | None = None,
                valid_to: int | None = None, is_default: bool = False, key: str | None = None) -> dict[str, Any]:
    _check_name(name)
    _check_range(valid_from, valid_to)
    if key is not None:
        _check_key(key)
    u = entities.uow(ctx, f"Thêm look {name}", "look.create")
    try:
        with u as s:
            a = entities.get(s, "asset", asset_id)
            u.project_id = a.project_id
            lk = _new_look(s, a, name, description_delta=description_delta, keep_features=keep_features,
                           lock_word=lock_word, valid_from=valid_from, valid_to=valid_to, is_default=is_default,
                           key=key)
            return look_out(s, lk)
    except IntegrityError as e:
        raise entities.integrity_error("asset_variant", e) from None


def _new_look(s: Session, a: Asset, name: str, *, description_delta: str = "", keep_features: list | None = None,
              lock_word: str = "", valid_from: int | None = None, valid_to: int | None = None,
              is_default: bool = False, key: str | None = None) -> AssetVariant:
    existing = set(s.scalars(select(AssetVariant.key).where(AssetVariant.asset_id == a.id)).all())
    if key is not None and key in existing:
        raise Conflict(f"Mã look '{key}' đã có trong tài sản này.", code="key_taken", hint="Chọn mã khác.")
    first = not _looks(s, a.id)
    lk = AssetVariant(asset_id=a.id, key=key or _free(existing, slugify(name, "look")), name=name,
                      description_delta=description_delta, keep_features=list(keep_features or []),
                      lock_word=lock_word, valid_from=valid_from, valid_to=valid_to,
                      is_default=bool(is_default or first))
    s.add(lk)
    s.flush()
    if lk.is_default:
        _unset_other_defaults(s, lk)
        s.flush()
    return lk


def look_update(ctx: OpContext, look_id: int, values: dict[str, Any],
                expected_updated_at: datetime | None = None) -> dict[str, Any]:
    if "asset_id" in values:
        raise AppError("readonly_field", "Không chuyển look sang tài sản khác.", status=422,
                       extra={"fields": ["asset_id"]})
    if "name" in values:
        _check_name(values["name"])
    u = entities.uow(ctx, f"Sửa look #{look_id}", "look.update")
    try:
        with u as s:
            lk = compiler.get_look(s, look_id)
            u.project_id = s.get(Asset, lk.asset_id).project_id
            _set(s, "asset_variant", lk, values, expected_updated_at)
            _check_range(lk.valid_from, lk.valid_to)
            if values.get("is_default") is True:
                _unset_other_defaults(s, lk)
            s.flush()
            return look_out(s, lk)
    except IntegrityError as e:
        raise entities.integrity_error("asset_variant", e) from None


def look_delete_preview(ctx: OpContext, look_id: int) -> dict[str, Any]:
    return entities.soft_delete_preview(ctx, "asset_variant", [look_id])


def look_delete(ctx: OpContext, confirm_token: str | None) -> dict[str, Any]:
    return entities.soft_delete(ctx, confirm_token, expect_type="asset_variant")


def look_set_anchor(ctx: OpContext, look_id: int, media_id: int | None) -> dict[str, Any]:
    u = entities.uow(ctx, f"Đặt ảnh mỏ neo cho look #{look_id}", "look.set_anchor")
    with u as s:
        lk = compiler.get_look(s, look_id)
        u.project_id = s.get(Asset, lk.asset_id).project_id
        if media_id is not None:
            _media_of_kind(s, media_id)
        lk.anchor_media_id = media_id
        s.flush()
        return look_out(s, lk)


def look_add_refs(ctx: OpContext, look_id: int, media_ids: list[int]) -> dict[str, Any]:
    u = entities.uow(ctx, f"Thêm ảnh tham chiếu cho look #{look_id}", "look.add_refs")
    with u as s:
        lk = compiler.get_look(s, look_id)
        a = s.get(Asset, lk.asset_id)
        u.project_id = a.project_id
        for mid in media_ids:
            m = _media_of_kind(s, mid)
            if (m.meta or {}).get("composite") or (m.meta or {}).get("sheet_grid"):
                raise AppError("composite_ref", f"Media #{mid} là sheet ghép nhiều góc: không dùng làm ảnh ref.",
                               hint="Tách từng góc thành ảnh riêng (mỗi ref một góc).", status=422)
        refs = [x for x in (lk.ref_media_ids or []) if isinstance(x, int)]
        refs += [x for x in media_ids if x not in refs]
        lk.ref_media_ids = refs
        s.flush()
        limit = int(compiler.setting(s, "library.look_max_refs", s.get(Project, a.project_id)))
        return look_out(s, lk, limit=limit)


# --- Kết nối ảnh + ước tính -----------------------------------------------------------------------------------------


def _image_target(ctx: OpContext, connection: int | str | None, project: Project | None = None):
    """(Connection, đang bật, adapter) của kết nối ảnh (bỏ trống = cài đặt image.connection, rồi kết nối ảnh đầu)."""
    from sagareel.providers import registry
    from sagareel.services.providers import ProviderService

    ps = ProviderService(ctx.services)
    with ctx.services.db.session() as s:
        if connection is None:
            connection = compiler.setting(s, "image.connection", project)
            found = s.scalars(select(ProviderConnection.id).where(
                ProviderConnection.name == str(connection), ProviderConnection.deleted_at.is_(None))).first()
            if found is None:
                found = s.scalar(select(ProviderConnection.id).where(
                    ProviderConnection.kind == "image", ProviderConnection.deleted_at.is_(None),
                    ProviderConnection.enabled.is_(True)).order_by(ProviderConnection.id).limit(1))
                if found is None:
                    raise AppError("no_connection", "Chưa có kết nối image nào.",
                                   hint="Thêm kết nối trong Cài đặt → Provider (provider.create).", status=422)
                connection = found
    conn, enabled = ps.find(connection)
    if conn.kind != "image":
        raise AppError("capability_mismatch", f"Kết nối '{conn.name}' là loại {conn.kind}, không tạo ảnh được.",
                       hint="Chọn một kết nối loại image (provider.list kind=image).", status=422)
    return conn, enabled, registry.get(conn.adapter)(conn)


def _image_params(ctx: OpContext, adapter, project: Project) -> dict[str, Any]:
    with ctx.services.db.session() as s:
        aspect = project.aspect
        caps = adapter.capabilities()
        if isinstance(caps.get("aspects"), list) and aspect not in caps["aspects"]:
            aspect = compiler.setting(s, "image.aspect", project)
        resolution = compiler.setting(s, "image.resolution", project)
    return {"aspect": aspect, "resolution": resolution, "model": adapter.conn.default_model or None}


async def _unit_estimate(adapter, params: dict[str, Any]) -> dict[str, Any]:
    from sagareel.providers.base import ImageRequest

    req = ImageRequest(prompt="estimate", refs=[], aspect=params["aspect"], resolution=params["resolution"],
                       model=params["model"], quality=None, out_dir=Path("dry_run"))
    return dict(await adapter.estimate(req))


# --- Sheet ------------------------------------------------------------------------------------------------------


def _normalize_views(asset: Asset, views: list[str] | None) -> list[str]:
    info = compiler.kind_info(asset.kind)
    views = list(dict.fromkeys(views or info["default_views"]))
    bad = [v for v in views if v not in info["views"]]
    if bad:
        raise AppError("view_unknown", f"Góc nhìn không có cho loại {asset.kind}: {', '.join(bad)}.",
                       hint=f"Chọn trong: {', '.join(info['views'])}.", status=422, extra={"views": list(info["views"])})
    if compiler.ANCHOR in views:  # anchor luôn đi trước
        views.remove(compiler.ANCHOR)
        views.insert(0, compiler.ANCHOR)
    return views


def submit_sheet_jobs(services, *, look_id: int, project_id: int, views: list[str], takes: int, conn_id: int,
                      lane: str, image_params: dict[str, Any], follow: dict | None = None,
                      unit: dict | None = None) -> list[dict[str, Any]]:
    """Tạo take (queued) + job `library.sheet` cho mỗi (góc, lần) trong một phiên khoá ghi. Trả [{take_id, job_id,
    view}]. `follow` (chỉ cho job anchor): góc còn lại gửi khi mỏ neo xong."""
    out = []
    datas = []
    with services.db.session(immediate=True) as s:
        for view in views:
            for _ in range(takes):
                t = Take(owner_type="variant", owner_id=look_id, part="sheet", view=view, status="queued",
                         inputs={"connection_id": conn_id, **image_params})
                s.add(t)
                s.flush()
                params = {"connection_id": conn_id, "look_id": look_id, "view": view, "take_id": t.id,
                          **image_params}
                if follow and view == compiler.ANCHOR:
                    params["follow"] = follow
                job = jobs.enqueue(s, SHEET_JOB, params, lane=lane, project_id=project_id,
                                   cost_estimate={"kind": "image", **(unit or {}), "view": view})
                s.flush()
                t.job_id = job.id
                datas.append(jobs.job_dict(job))
                out.append({"take_id": t.id, "job_id": job.id, "view": view})
    for d in datas:
        jobs.notify(services, d)
    return out


async def sheet_generate(ctx: OpContext, look_id: int, views: list[str] | None = None, takes: int = 1,
                         connection: int | str | None = None, dry_run: bool = False) -> dict[str, Any]:
    import anyio

    from sagareel.services.providers import ProviderService

    if not 1 <= takes <= MAX_TAKES:
        raise _invalid(f"takes phải từ 1 tới {MAX_TAKES}.", "takes")

    def load() -> tuple[AssetVariant, Asset, Project]:
        with ctx.services.db.session() as s:
            lk = compiler.get_look(s, look_id)
            a = s.get(Asset, lk.asset_id)
            return lk, a, s.get(Project, a.project_id)

    look, asset, project = await anyio.to_thread.run_sync(load)
    norm = _normalize_views(asset, views)
    conn, enabled, adapter = await anyio.to_thread.run_sync(_image_target, ctx, connection, project)
    params = await anyio.to_thread.run_sync(_image_params, ctx, adapter, project)
    unit = await _unit_estimate(adapter, params)
    units_each = float(unit.get("units", 1) or 1)
    has_anchor = look.anchor_media_id is not None
    if not has_anchor and compiler.ANCHOR not in norm:
        raise AppError("anchor_missing", f"Look '{look.name}' chưa có ảnh mỏ neo.",
                       hint="Thêm góc 'anchor' vào views (hoặc look.set_anchor) — mọi góc khác chỉ tham chiếu mỏ neo.",
                       status=409)
    count = len(norm) * takes
    estimate = {"kind": "image", "connection": conn.name, "connection_id": conn.id, "adapter": adapter.adapter_id,
                "enabled": enabled, "views": norm, "takes": takes, "images": count,
                "units": units_each * count, "cost": (unit.get("cost") or 0) * count if unit.get("cost") is not None
                else None, **params}

    def run() -> dict[str, Any]:
        with ctx.services.db.session() as s:
            if dry_run:
                estimate["budget"] = budget.status(s, "image", resolver=ctx.services.resolver)
                return {"estimate": estimate}
            budget.check(s, "image", units_each * count, conn.id, resolver=ctx.services.resolver)
        lane = ProviderService.lane(conn)
        if has_anchor or compiler.ANCHOR not in norm:
            submitted = submit_sheet_jobs(ctx.services, look_id=look_id, project_id=project.id, views=norm,
                                          takes=takes, conn_id=conn.id, lane=lane, image_params=params, unit=unit)
            deferred: list[str] = []
        else:
            rest = [v for v in norm if v != compiler.ANCHOR]
            follow = {"views": rest, "takes": takes} if rest else None
            submitted = submit_sheet_jobs(ctx.services, look_id=look_id, project_id=project.id,
                                          views=[compiler.ANCHOR], takes=takes, conn_id=conn.id, lane=lane,
                                          image_params=params, follow=follow, unit=unit)
            deferred = rest
        return {"jobs": submitted, "job_ids": [x["job_id"] for x in submitted],
                "take_ids": [x["take_id"] for x in submitted], "deferred_views": deferred, "estimate": estimate}

    return await anyio.to_thread.run_sync(run)


def sheet_select(ctx: OpContext, look_id: int, view: str, take_id: int) -> dict[str, Any]:
    u = entities.uow(ctx, f"Chọn take sheet {view} cho look #{look_id}", "sheet.select")
    with u as s:
        lk = compiler.get_look(s, look_id)
        u.project_id = s.get(Asset, lk.asset_id).project_id
        t = s.get(Take, take_id)
        if t is None or t.owner_type != "variant" or t.owner_id != look_id or t.part != "sheet" or t.view != view:
            raise NotFound(f"Không có take sheet #{take_id} cho góc '{view}' của look #{look_id}.",
                           code="take_not_found")
        if t.status != "succeeded" or t.media_id is None:
            raise AppError("take_not_ready", f"Take #{take_id} chưa tạo xong ({t.status}).", status=409)
        apply_sheet_media(lk, view, t.media_id)
        s.flush()
        return look_out(s, lk)


def apply_sheet_media(lk: AssetVariant, view: str, media_id: int) -> None:
    if view == compiler.ANCHOR:
        lk.anchor_media_id = media_id
    else:
        lk.sheet = {**dict(lk.sheet or {}), view: media_id}


def sheet_list(ctx: OpContext, look_id: int) -> dict[str, Any]:
    with ctx.services.db.session() as s:
        lk = compiler.get_look(s, look_id)
        rows = s.scalars(select(Take).where(Take.owner_type == "variant", Take.owner_id == look_id,
                                            Take.part == "sheet").order_by(Take.id)).all()
        selected = {compiler.ANCHOR: lk.anchor_media_id, **dict(lk.sheet or {})}
        views: dict[str, list] = {}
        for t in rows:
            d = take_dict(t)
            d["selected"] = t.media_id is not None and selected.get(t.view) == t.media_id
            views.setdefault(t.view, []).append(d)
        return {"look": look_out(s, lk), "views": views, "selected": selected}


# --- Ma trận look -------------------------------------------------------------------------------------------------


async def look_generate_matrix(ctx: OpContext, asset_id: int, outfits: list[str], expressions: list[str],
                               generate_sheets: bool = False, views: list[str] | None = None, takes: int = 1,
                               connection: int | str | None = None, dry_run: bool = False) -> dict[str, Any]:
    """Look con có tên cho mỗi (trang phục × biểu cảm); `description_delta` = các phần ghép nguyên văn. Look trùng tên
    bỏ qua. `generate_sheets` → gửi sheet (anchor trước) cho mỗi look mới; luôn ước tính trước."""
    import anyio

    outfits = [o for o in outfits if isinstance(o, str) and o.strip()]
    expressions = [e for e in expressions if isinstance(e, str) and e.strip()]
    if not outfits and not expressions:
        raise _invalid("Cần ít nhất một trang phục hoặc một biểu cảm.", "outfits")
    combos = [(o, e) for o in (outfits or [""]) for e in (expressions or [""])]
    if len(combos) > 50:
        raise _invalid("Ma trận tối đa 50 look mỗi lần.", "outfits")

    def plan() -> tuple[Asset, Project, list[dict[str, Any]], AssetVariant | None]:
        with ctx.services.db.session() as s:
            a = entities.get(s, "asset", asset_id)
            names = {lk.name for lk in _looks(s, a.id)}
            base, _w = compiler.select_look(s, a, None)
            items = []
            for o, e in combos:
                name = " · ".join(x for x in (o, e) if x)
                items.append({"name": name, "description_delta": ", ".join(x for x in (o, e) if x),
                              "outfit": o, "expression": e, "exists": name in names})
            return a, s.get(Project, a.project_id), items, base

    asset, project, items, base = await anyio.to_thread.run_sync(plan)
    new_items = [it for it in items if not it["exists"]]
    estimate: dict[str, Any] = {"looks": len(new_items), "images": 0, "units": 0}
    if generate_sheets and new_items:
        norm = _normalize_views(asset, views)
        conn, enabled, adapter = await anyio.to_thread.run_sync(_image_target, ctx, connection, project)
        params = await anyio.to_thread.run_sync(_image_params, ctx, adapter, project)
        unit = await _unit_estimate(adapter, params)
        images = len(norm) * takes * len(new_items)
        estimate.update({"images": images, "units": float(unit.get("units", 1) or 1) * images, "views": norm,
                         "takes": takes, "connection": conn.name, "enabled": enabled})
        if dry_run:
            with ctx.services.db.session() as s:
                estimate["budget"] = budget.status(s, "image", resolver=ctx.services.resolver)
    if dry_run:
        return {"items": items, "estimate": estimate}

    def create() -> list[int]:
        ids = []
        u = entities.uow(ctx, f"Tạo ma trận look cho {asset.name}", "look.generate_matrix")
        u.project_id = asset.project_id
        with u as s:
            a = entities.get(s, "asset", asset_id)
            for it in new_items:
                lk = _new_look(s, a, it["name"], description_delta=it["description_delta"],
                               keep_features=list(base.keep_features or []) if base else [],
                               lock_word=base.lock_word if base else "")
                ids.append(lk.id)
        return ids

    look_ids = await anyio.to_thread.run_sync(create)
    jobs_out = []
    if generate_sheets:
        for lid in look_ids:
            res = await sheet_generate(ctx, lid, views=views, takes=takes, connection=connection)
            jobs_out.extend(res["jobs"])
    return {"items": items, "look_ids": look_ids, "jobs": jobs_out, "estimate": estimate}


# --- Phía job: sheet ----------------------------------------------------------------------------------------------


def sheet_job_prepare(services, take_id: int, look_id: int, view: str, conn_id: int) -> dict[str, Any]:
    """Compile prompt góc sheet, đánh dấu take running. Góc khác anchor mà look chưa có mỏ neo → `anchor_missing`."""
    from sagareel.services.jobs import JobFailed

    with services.db.session(immediate=True) as s:
        lk = compiler.get_look(s, look_id)
        asset = s.get(Asset, lk.asset_id)
        if view != compiler.ANCHOR and lk.anchor_media_id is None:
            raise JobFailed("anchor_missing", f"Look '{lk.name}' chưa có ảnh mỏ neo để làm ref cho góc '{view}'.",
                            hint="Tạo và chọn góc anchor trước.")
        cp = compiler.compile_sheet(s, look_id, view, connection=conn_id)
        paths = [media.file_path(services.settings, _media_of_kind(s, r.media_id)) for r in cp.refs]
        t = s.get(Take, take_id)
        t.status = "running"
        t.inputs = {**dict(t.inputs or {}), "prompt": cp.text, "ref_media_ids": [r.media_id for r in cp.refs],
                    "refs": compiler.refs_of(cp.refs), "warnings": list(cp.warnings), "view": view}
        return {"prompt": cp.text, "refs": paths, "ref_media_ids": [r.media_id for r in cp.refs],
                "project_id": asset.project_id}


def take_finish(services, take_id: int, *, status: str, media_id: int | None = None, cost: float = 0.0,
                elapsed_ms: int | None = None, error: str | None = None) -> None:
    with services.db.session(immediate=True) as s:
        t = s.get(Take, take_id)
        if t is None:
            return
        t.status = status
        if media_id is not None:
            t.media_id = media_id
        t.cost = cost
        t.elapsed_ms = elapsed_ms
        if error:
            t.inputs = {**dict(t.inputs or {}), "error": error}


def sheet_job_done(services, job_id: int, take_id: int, look_id: int, view: str, media_id: int,
                   follow: dict | None, conn_id: int, lane: str, image_params: dict) -> dict[str, Any]:
    """Tự chọn take đầu tiên của mỗi góc (mỏ neo/sheet còn trống). Job anchor đặt mỏ neo → gửi các góc trong `follow`."""
    set_anchor = False
    project_id = None
    u = UnitOfWork(services.db, actor=SYSTEM_ACTOR, op_name=SHEET_JOB, summary=f"Sheet look #{look_id}: {view}")
    with u as s:
        lk = s.get(AssetVariant, look_id)
        if lk is None or lk.deleted_at is not None:
            return {"selected": False, "follow_jobs": []}
        project_id = s.get(Asset, lk.asset_id).project_id
        u.project_id = project_id
        selected = False
        if view == compiler.ANCHOR:
            if lk.anchor_media_id is None:
                lk.anchor_media_id = media_id
                selected = set_anchor = True
        elif (lk.sheet or {}).get(view) is None:
            apply_sheet_media(lk, view, media_id)
            selected = True
    follow_jobs: list[dict] = []
    if set_anchor and follow and follow.get("views"):
        follow_jobs = submit_sheet_jobs(services, look_id=look_id, project_id=project_id,
                                        views=list(follow["views"]), takes=int(follow.get("takes", 1)),
                                        conn_id=conn_id, lane=lane, image_params=image_params)
    return {"selected": selected, "follow_jobs": follow_jobs}


# --- Drift check + chấm take ----------------------------------------------------------------------------------------


def _check_ids(ids: list[int], what: str) -> list[int]:
    clean = list(dict.fromkeys(int(i) for i in ids))
    if len(clean) > MAX_IDS:
        raise _invalid(f"Tối đa {MAX_IDS} {what} mỗi lần.", what)
    return clean


def _llm_lane(s: Session, task_key: str) -> tuple[str, dict]:
    p = llm.plan(s, task_key, {"input": "-"}, images=True)
    route, conn = p.candidates[0]
    return f"conn:{conn.id}", {"kind": "llm", "units": 1, "task": task_key, "route": route.to_dict(),
                               "connection_id": conn.id}


def _targets(s: Session, clip_ids: list[int], take_ids: list[int]) -> tuple[list[tuple[Take, Clip | None]], list]:
    out: list[tuple[Take, Clip | None]] = []
    skipped = []
    for cid in clip_ids:
        clip = s.get(Clip, cid)
        if clip is None or clip.deleted_at is not None:
            skipped.append({"clip_id": cid, "reason": "not_found"})
            continue
        tid = clip.video_take_id or clip.image_take_id
        t = s.get(Take, tid) if tid else None
        if t is None or t.media_id is None:
            skipped.append({"clip_id": cid, "reason": "no_selected_take"})
            continue
        out.append((t, clip))
    for tid in take_ids:
        t = s.get(Take, tid)
        if t is None:
            skipped.append({"take_id": tid, "reason": "not_found"})
            continue
        if t.media_id is None:
            skipped.append({"take_id": tid, "reason": "no_media"})
            continue
        clip = s.get(Clip, t.owner_id) if t.owner_type == "clip" else None
        if t.owner_type not in ("clip", "variant"):
            skipped.append({"take_id": tid, "reason": "unsupported_owner"})
            continue
        out.append((t, clip))
    seen: set[int] = set()
    uniq = []
    for t, c in out:
        if t.id not in seen:
            seen.add(t.id)
            uniq.append((t, c))
    return uniq, skipped


def _project_of_take(s: Session, t: Take, clip: Clip | None) -> int | None:
    if clip is not None:
        return s.get(Sequence, clip.sequence_id).project_id
    if t.owner_type == "variant":
        lk = s.get(AssetVariant, t.owner_id)
        return s.get(Asset, lk.asset_id).project_id if lk else None
    return None


def submit_llm_checks(ctx: OpContext, job_type: str, task_key: str, clip_ids: list[int],
                      take_ids: list[int]) -> dict[str, Any]:
    clip_ids = _check_ids(clip_ids, "clip_ids")
    take_ids = _check_ids(take_ids, "take_ids")
    if not clip_ids and not take_ids:
        raise _invalid("Cần ít nhất một clip_id hoặc take_id.", "clip_ids")
    datas = []
    with ctx.services.db.session(immediate=True) as s:
        lane, estimate = _llm_lane(s, task_key)
        targets, skipped = _targets(s, clip_ids, take_ids)
        out = []
        for t, clip in targets:
            params = {"take_id": t.id, "clip_id": clip.id if clip else None}
            job = jobs.enqueue(s, job_type, params, lane=lane, project_id=_project_of_take(s, t, clip),
                               clip_id=clip.id if clip else None, cost_estimate=estimate)
            s.flush()
            datas.append(jobs.job_dict(job))
            out.append({"take_id": t.id, "clip_id": clip.id if clip else None, "job_id": job.id})
    for d in datas:
        jobs.notify(ctx.services, d)
    return {"jobs": out, "job_ids": [x["job_id"] for x in out], "skipped": skipped}


def score100(value: Any) -> int | None:
    """Điểm 0–100 từ câu trả lời LLM (số thực 0–1 coi là tỉ lệ)."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if isinstance(value, float) and 0.0 <= v <= 1.0:
        v *= 100
    return round(min(100.0, max(0.0, v)))


def check_inputs(services, take_id: int, clip_id: int | None) -> dict[str, Any]:
    """Đọc trước cho job drift/score: media cần xem, ảnh mỏ neo tham chiếu, mô tả nhân vật, prompt của take."""
    with services.db.session() as s:
        t = s.get(Take, take_id)
        if t is None or t.media_id is None:
            raise NotFound(f"Take #{take_id} không có media để kiểm tra.", code="take_not_found")
        m = media.get(s, t.media_id)
        refs: list[dict[str, Any]] = []
        characters = []
        if clip_id is not None:
            for asset, look in compiler.clip_assets_looks(s, clip_id):
                mid = (look.anchor_media_id if look else None) or asset.cover_media_id
                characters.append({"name": asset.name, "kind": asset.kind, "description": asset.description,
                                   "lock_word": look.lock_word if look else ""})
                if mid is not None:
                    refs.append({"media_id": mid, "name": asset.name, "kind": asset.kind})
        elif t.owner_type == "variant":
            lk = s.get(AssetVariant, t.owner_id)
            if lk is not None:
                asset = s.get(Asset, lk.asset_id)
                characters.append({"name": asset.name, "kind": asset.kind, "description": asset.description,
                                   "lock_word": lk.lock_word})
                if lk.anchor_media_id is not None and lk.anchor_media_id != t.media_id:
                    refs.append({"media_id": lk.anchor_media_id, "name": asset.name, "kind": asset.kind})
        ref_paths = []
        for r in refs:
            rm = s.get(Media, r["media_id"])
            if rm is not None and rm.kind == "image":
                ref_paths.append((r, media.file_path(services.settings, rm)))
        return {"media_id": m.id, "media_kind": m.kind, "path": media.file_path(services.settings, m),
                "refs": ref_paths, "characters": characters, "prompt": str((t.inputs or {}).get("prompt") or "")}


def drift_input(info: dict[str, Any]) -> str:
    lines = ["Continuity check against locked references."]
    for i, (r, _p) in enumerate(info["refs"], start=1):
        lines.append(f"Image {i}: anchor reference of {r['name']} ({r['kind']}).")
    lines.append(f"Image {len(info['refs']) + 1} (the last image): the frame to check.")
    for c in info["characters"]:
        lw = f" Lock word: {c['lock_word']}." if c.get("lock_word") else ""
        lines.append(f"{c['name']} ({c['kind']}): {c['description']}{lw}")
    lines.append("Fixed checklist, check every item: " + ", ".join(DRIFT_CHECKLIST) + ".")
    lines.append('Reply with JSON {"score": <integer 0-100, 100 = identical identity and design>, '
                 '"issues": [{"entity": "...", "aspect": "<checklist item>", "severity": "low|medium|high", '
                 '"detail": "...", "fix_hint": "..."}], "reason": "<one sentence>"}.')
    return "\n".join(lines)


def score_input(info: dict[str, Any]) -> str:
    lines = ["Score this generated take.", f"Prompt used: {info['prompt'] or '(unknown)'}"]
    for c in info["characters"]:
        lines.append(f"Expected {c['kind']} {c['name']}: {c['description']}")
    if info["refs"]:
        lines.append(f"Images 1..{len(info['refs'])} are references; the last image is the take.")
    lines.append('Scores are integers 0-100. JSON: {"score": 0, "prompt_match": 0, "character_match": 0, '
                 '"defects": [{"type": "hands", "severity": "low", "detail": "..."}], "verdict": "keep|regenerate"}.')
    return "\n".join(lines)


def save_drift(services, take_id: int, clip_id: int | None, result: dict[str, Any]) -> dict[str, Any]:
    """Ghi `take.auto_score.drift`; clip: điểm < ngưỡng → `flags.drift`, ≥ ngưỡng → gỡ cờ drift."""
    flagged = None
    with services.db.session(immediate=True) as s:
        t = s.get(Take, take_id)
        t.auto_score = {**dict(t.auto_score or {}), "drift": result}
    if clip_id is None:
        return {"flagged": None}
    with services.db.session() as s:
        clip = s.get(Clip, clip_id)
        seq = s.get(Sequence, clip.sequence_id)
        project = s.get(Project, seq.project_id)
        threshold = int(compiler.setting(s, "library.drift_threshold", project, seq, clip))
    u = UnitOfWork(services.db, actor=SYSTEM_ACTOR, op_name=DRIFT_JOB, project_id=project.id,
                   summary=f"Drift check clip #{clip_id}")
    with u as s:
        clip = s.get(Clip, clip_id)
        flags = dict(clip.flags or {})
        if result["score"] < threshold:
            flags["drift"] = {"score": result["score"], "reason": result["reason"], "take_id": take_id,
                              "issues": result["issues"][:10], "at": utcnow().isoformat()}
            flagged = True
        else:
            flags.pop("drift", None)
            flagged = False
        if flags != dict(clip.flags or {}):
            clip.flags = flags
    return {"flagged": flagged, "threshold": threshold}


def save_score(services, take_id: int, result: dict[str, Any]) -> None:
    with services.db.session(immediate=True) as s:
        t = s.get(Take, take_id)
        t.auto_score = {**dict(t.auto_score or {}), "score": result}


# --- Mô tả từ ảnh -------------------------------------------------------------------------------------------------


def describe_from_image(ctx: OpContext, media_id: int, kind: str = "character", hint: str = "",
                        project_id: int | None = None) -> dict[str, Any]:
    text = f"Kind: {kind}."
    if hint:
        text += f"\nName or note from the user: {hint}"
    job = llm.submit_run(ctx.services, "describe_asset", {"input": text}, override=None,
                         images_media_ids=[media_id], project_id=project_id)
    return {"job_id": job["id"]}
