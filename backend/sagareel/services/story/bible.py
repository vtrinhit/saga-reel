"""Story Bible có bằng chứng (spec §23.9): `bible_item` + `bible_evidence` + FTS5 `bible_fts`.

- Mỗi mục: `type` ∈ TYPES, `key` (duy nhất theo project + type, kể cả mục đã xoá mềm), `summary`, `attributes`,
  `status` pending|confirmed|rejected, `locked` (LLM không được sửa — "nhãn nhân thiết"), `story_order` (số tập),
  `asset_id?`.
- FTS: bảng content-less nên service tự ghi chỉ mục: `DELETE WHERE rowid=?` rồi `INSERT` lại mỗi khi mục/bằng chứng
  đổi. Chữ được **bỏ dấu (đ→d) ở cả chỉ mục lẫn truy vấn** (tokenizer `remove_diacritics` không đổi được đ). Kết quả
  tìm luôn nối ngược về `bible_item` (lọc đã xoá/bị từ chối) — `history.undo` không khôi phục chỉ mục FTS nên không
  bao giờ tin riêng chỉ mục.
- Ghi qua UnitOfWork (hoàn tác được); bằng chứng là bảng `Plain` (không có lịch sử).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sagareel.core.context import OpContext
from sagareel.core.errors import AppError, ValidationFailed
from sagareel.db.models import Asset, BibleEvidence, BibleItem, Sequence
from sagareel.services import entities
from sagareel.services.story import text as T

TYPES = ("character", "location", "relationship", "event", "storyline", "fact", "open_question")
STATUSES = ("pending", "confirmed", "rejected")
SOURCES = ("script", "novel", "manual", "llm")
EDITABLE = frozenset({"type", "key", "summary", "attributes", "status", "locked", "story_order", "asset_id"})
MAX_LIST = 500


# --- Chỉ mục FTS ------------------------------------------------------------------------------------------------


def _attr_text(attrs: dict[str, Any] | None) -> str:
    parts: list[str] = []
    for k, v in (attrs or {}).items():
        if isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False)
        parts.append(f"{k} {v}")
    return " ".join(parts)


def reindex(s: Session, item: BibleItem) -> None:
    """Ghi lại dòng FTS của một mục (xoá rồi chèn); mục đã xoá mềm thì chỉ xoá."""
    s.flush()
    s.execute(text("DELETE FROM bible_fts WHERE rowid = :id"), {"id": item.id})
    if item.deleted_at is not None:
        return
    quotes = s.scalars(select(BibleEvidence.quote).where(BibleEvidence.item_id == item.id)
                       .order_by(BibleEvidence.id)).all()
    summary = T.fold(f"{item.key.replace('_', ' ')} {item.summary} {_attr_text(item.attributes)}")
    s.execute(text("INSERT INTO bible_fts(rowid, summary, quote) VALUES (:id, :summary, :quote)"),
              {"id": item.id, "summary": summary, "quote": T.fold(" \n".join(quotes))})


def reindex_project(s: Session, project_id: int) -> int:
    rows = s.scalars(select(BibleItem).where(BibleItem.project_id == project_id)).all()
    for r in rows:
        reindex(s, r)
    return len(rows)


# --- Đọc ----------------------------------------------------------------------------------------------------------


def evidence_dict(e: BibleEvidence) -> dict[str, Any]:
    return {"id": e.id, "item_id": e.item_id, "sequence_id": e.sequence_id, "source": e.source, "quote": e.quote,
            "start": e.start, "end": e.end, "created_at": e.created_at.isoformat() if e.created_at else None}


def item_dict(s: Session, item: BibleItem, *, evidence: bool = False) -> dict[str, Any]:
    out = entities.to_dict(item)
    n = s.scalar(select(func.count()).select_from(BibleEvidence).where(BibleEvidence.item_id == item.id)) or 0
    out["evidence_count"] = int(n)
    if evidence:
        rows = s.scalars(select(BibleEvidence).where(BibleEvidence.item_id == item.id)
                         .order_by(BibleEvidence.id)).all()
        out["evidence"] = [evidence_dict(e) for e in rows]
    return out


def get_item(s: Session, item_id: int) -> BibleItem:
    return entities.get(s, "bible_item", item_id)


def get(ctx: OpContext, item_id: int) -> dict[str, Any]:
    with ctx.services.db.session() as s:
        return item_dict(s, get_item(s, item_id), evidence=True)


def list_items(ctx: OpContext, project_id: int, *, type_: str | None = None, status: str | None = None,
               locked: bool | None = None, max_order: int | None = None, cursor: int | None = None,
               limit: int = 200) -> dict[str, Any]:
    limit = max(1, min(int(limit), MAX_LIST))
    with ctx.services.db.session() as s:
        entities.get(s, "project", project_id)
        q = select(BibleItem).where(BibleItem.project_id == project_id, BibleItem.deleted_at.is_(None))
        if type_ is not None:
            q = q.where(BibleItem.type == type_)
        if status is not None:
            q = q.where(BibleItem.status == status)
        if locked is not None:
            q = q.where(BibleItem.locked.is_(locked))
        if max_order is not None:
            q = q.where((BibleItem.story_order.is_(None)) | (BibleItem.story_order <= max_order))
        if cursor is not None:
            q = q.where(BibleItem.id > cursor)
        rows = s.scalars(q.order_by(BibleItem.id).limit(limit + 1)).all()
        more = len(rows) > limit
        rows = rows[:limit]
        return {"items": [item_dict(s, r) for r in rows], "next_cursor": rows[-1].id if more and rows else None}


def search_ids(s: Session, project_id: int, query: str, *, mode: str = "and", limit: int = 20,
               types: list[str] | None = None, statuses: tuple[str, ...] = ("pending", "confirmed"),
               max_order: int | None = None) -> list[tuple[int, float]]:
    """[(id, điểm bm25 — nhỏ hơn là khớp hơn)] của các mục chưa xoá khớp `query` (bỏ dấu, đ→d)."""
    q = T.fts_query(query, mode=mode)
    if not q:
        return []
    sql = ("SELECT b.id AS id, bm25(bible_fts) AS score FROM bible_fts JOIN bible_item b ON b.id = bible_fts.rowid "
           "WHERE bible_fts MATCH :q AND b.project_id = :p AND b.deleted_at IS NULL")
    params: dict[str, Any] = {"q": q, "p": project_id, "n": int(limit)}
    if statuses:
        sql += " AND b.status IN (" + ",".join(f":s{i}" for i in range(len(statuses))) + ")"
        params.update({f"s{i}": st for i, st in enumerate(statuses)})
    if types:
        sql += " AND b.type IN (" + ",".join(f":t{i}" for i in range(len(types))) + ")"
        params.update({f"t{i}": t for i, t in enumerate(types)})
    if max_order is not None:
        sql += " AND (b.story_order IS NULL OR b.story_order <= :mo)"
        params["mo"] = int(max_order)
    sql += " ORDER BY score LIMIT :n"
    return [(int(r.id), float(r.score)) for r in s.execute(text(sql), params)]


def search(ctx: OpContext, project_id: int, q: str, *, types: list[str] | None = None,
           statuses: list[str] | None = None, limit: int = 20) -> dict[str, Any]:
    if not q or not T.words(q):
        raise ValidationFailed("Cần từ khoá để tìm trong Story Bible.")
    sts = tuple(statuses) if statuses else ("pending", "confirmed")
    with ctx.services.db.session() as s:
        entities.get(s, "project", project_id)
        hits = search_ids(s, project_id, q, limit=limit, types=types, statuses=sts)
        mode = "and"
        if not hits:  # không mục nào chứa đủ mọi từ → nới thành OR
            hits = search_ids(s, project_id, q, mode="or", limit=limit, types=types, statuses=sts)
            mode = "or"
        items = []
        for item_id, score in hits:
            d = item_dict(s, s.get(BibleItem, item_id), evidence=True)
            d["score"] = round(-score, 4)
            items.append(d)
    return {"items": items, "query": T.fts_query(q, mode=mode), "mode": mode}


# --- Ghi ----------------------------------------------------------------------------------------------------------


def _check(values: dict[str, Any]) -> None:
    errors = []
    if "type" in values and values["type"] not in TYPES:
        errors.append({"loc": ["type"], "msg": f"Loại phải là một trong: {', '.join(TYPES)}.", "type": "enum"})
    if "status" in values and values["status"] not in STATUSES:
        errors.append({"loc": ["status"], "msg": f"Trạng thái phải là một trong: {', '.join(STATUSES)}.",
                       "type": "enum"})
    if "key" in values and (not isinstance(values["key"], str) or not values["key"].strip()):
        errors.append({"loc": ["key"], "msg": "Mã mục không được để trống.", "type": "value"})
    if errors:
        raise ValidationFailed("Giá trị mục Story Bible không hợp lệ.", hint="Xem `extra.errors`.",
                               extra={"errors": errors})


def _check_asset(s: Session, project_id: int, asset_id: int | None) -> None:
    if asset_id is None:
        return
    a = s.get(Asset, asset_id)
    if a is None or a.deleted_at is not None or a.project_id != project_id:
        raise ValidationFailed(f"Tài sản #{asset_id} không thuộc dự án #{project_id}.")


def _check_sequence(s: Session, project_id: int, sequence_id: int | None) -> None:
    if sequence_id is None:
        return
    q = s.get(Sequence, sequence_id)
    if q is None or q.deleted_at is not None or q.project_id != project_id:
        raise ValidationFailed(f"Tập #{sequence_id} không thuộc dự án #{project_id}.")


def _key_taken(type_: str, key: str) -> AppError:
    return AppError("key_taken", f"Mã '{key}' đã có trong Story Bible (loại {type_}).",
                    hint="Chọn mã khác, hoặc sửa mục cũ (bible.update).", status=409)


def find(s: Session, project_id: int, type_: str, key: str) -> BibleItem | None:
    """Mục theo (project, type, key) — kể cả đã xoá mềm (ràng buộc unique tính cả chúng)."""
    return s.scalars(select(BibleItem).where(BibleItem.project_id == project_id, BibleItem.type == type_,
                                             BibleItem.key == key)).first()


def add_evidence(s: Session, item: BibleItem, quote: str, *, source: str = "manual", sequence_id: int | None = None,
                 start: int | None = None, end: int | None = None) -> BibleEvidence | None:
    quote = (quote or "").strip()
    if not quote:
        return None
    if source not in SOURCES:
        raise ValidationFailed(f"Nguồn bằng chứng phải là một trong: {', '.join(SOURCES)}.")
    same_seq = (BibleEvidence.sequence_id.is_(None) if sequence_id is None
                else BibleEvidence.sequence_id == sequence_id)
    dup = s.scalars(select(BibleEvidence).where(BibleEvidence.item_id == item.id, BibleEvidence.quote == quote,
                                                same_seq)).first()
    if dup is not None:
        return dup
    e = BibleEvidence(item_id=item.id, sequence_id=sequence_id, source=source, quote=quote, start=start, end=end)
    s.add(e)
    s.flush()
    return e


def create(ctx: OpContext, project_id: int, type_: str, summary: str, *, key: str | None = None,
           attributes: dict[str, Any] | None = None, status: str = "confirmed", locked: bool = False,
           story_order: int | None = None, asset_id: int | None = None,
           evidence: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Tạo mục (người dùng/agent tạo tay → mặc định `confirmed`)."""
    key = (key or T.slug_key(summary, default=type_)).strip()
    _check({"type": type_, "status": status, "key": key})
    u = entities.uow(ctx, f"Thêm mục Story Bible {type_}:{key}", "bible.create")
    try:
        with u as s:
            entities.get(s, "project", project_id)
            u.project_id = project_id
            _check_asset(s, project_id, asset_id)
            if find(s, project_id, type_, key) is not None:
                raise _key_taken(type_, key)
            item = BibleItem(project_id=project_id, type=type_, key=key, summary=summary or "",
                             attributes=dict(attributes or {}), status=status, locked=bool(locked),
                             story_order=story_order, asset_id=asset_id)
            s.add(item)
            s.flush()
            for ev in evidence or []:
                _check_sequence(s, project_id, ev.get("sequence_id"))
                add_evidence(s, item, ev.get("quote", ""), source=ev.get("source") or "manual",
                             sequence_id=ev.get("sequence_id"), start=ev.get("start"), end=ev.get("end"))
            reindex(s, item)
            out = item_dict(s, item, evidence=True)
    except IntegrityError as e:
        raise entities.integrity_error("bible_item", e) from None
    return out


def update(ctx: OpContext, item_id: int, values: dict[str, Any],
           expected_updated_at: datetime | None = None) -> dict[str, Any]:
    bad = sorted(set(values) - EDITABLE)
    if bad:
        raise AppError("readonly_field", f"Không sửa được trường: {', '.join(bad)}.", status=422,
                       extra={"fields": bad})
    _check(values)
    clean = entities.check_values("bible_item", values)
    u = entities.uow(ctx, f"Sửa mục Story Bible #{item_id}", "bible.update")
    try:
        with u as s:
            item = get_item(s, item_id)
            entities.check_fresh("bible_item", item, expected_updated_at)
            u.project_id = item.project_id
            if "asset_id" in clean:
                _check_asset(s, item.project_id, clean["asset_id"])
            new_type, new_key = clean.get("type", item.type), clean.get("key", item.key)
            if (new_type, new_key) != (item.type, item.key):
                other = find(s, item.project_id, new_type, new_key)
                if other is not None and other.id != item.id:
                    raise _key_taken(new_type, new_key)
            for k, v in clean.items():
                setattr(item, k, v)
            reindex(s, item)
            out = item_dict(s, item, evidence=True)
    except IntegrityError as e:
        raise entities.integrity_error("bible_item", e) from None
    return out


def set_status(ctx: OpContext, ids: list[int], status: str, *, lock: bool | None = None) -> dict[str, Any]:
    """Xác nhận / từ chối hàng loạt (một change set)."""
    if status not in STATUSES:
        raise ValidationFailed(f"Trạng thái phải là một trong: {', '.join(STATUSES)}.")
    ids = sorted(set(ids))
    if not ids:
        raise ValidationFailed("Cần ít nhất một id.")
    verb = {"confirmed": "Xác nhận", "rejected": "Từ chối", "pending": "Đưa về chờ duyệt"}[status]
    u = entities.uow(ctx, f"{verb} {len(ids)} mục Story Bible", f"bible.{'confirm' if status == 'confirmed' else 'reject'}")
    with u as s:
        rows = [get_item(s, i) for i in ids]
        projects = {r.project_id for r in rows}
        u.project_id = projects.pop() if len(projects) == 1 else None
        for r in rows:
            r.status = status
            if lock is not None:
                r.locked = bool(lock)
            reindex(s, r)
        out = [item_dict(s, r) for r in rows]
    return {"items": out}


def delete_preview(ctx: OpContext, ids: list[int]) -> dict[str, Any]:
    return entities.soft_delete_preview(ctx, "bible_item", ids)


def delete(ctx: OpContext, confirm_token: str | None) -> dict[str, Any]:
    out = entities.soft_delete(ctx, confirm_token, expect_type="bible_item")
    with ctx.services.db.session() as s:
        for i in out["deleted"]:
            s.execute(text("DELETE FROM bible_fts WHERE rowid = :id"), {"id": i})
    return out


def evidence_add(ctx: OpContext, item_id: int, quote: str, *, source: str = "manual", sequence_id: int | None = None,
                 start: int | None = None, end: int | None = None) -> dict[str, Any]:
    if not (quote or "").strip():
        raise ValidationFailed("Trích dẫn bằng chứng không được để trống.")
    with ctx.services.db.session() as s:
        item = get_item(s, item_id)
        _check_sequence(s, item.project_id, sequence_id)
        e = add_evidence(s, item, quote, source=source, sequence_id=sequence_id, start=start, end=end)
        reindex(s, item)
        return {"evidence": evidence_dict(e), "item": item_dict(s, item)}


def upsert_proposal(s: Session, project_id: int, type_: str, key: str, summary: str, *,
                    attributes: dict[str, Any] | None = None, story_order: int | None = None,
                    asset_id: int | None = None, evidence: list[dict[str, Any]] | None = None,
                    status: str = "pending") -> tuple[BibleItem, str]:
    """Đề xuất của LLM (trong UoW của người gọi). Trả (mục, hành động) — hành động ∈ created|updated|evidence|skipped.

    - Chưa có → tạo `pending` (hoặc `status` truyền vào).
    - Đã có, chưa xoá: thêm bằng chứng; chỉ sửa `summary`/`attributes` khi mục chưa khoá và còn `pending`
      (mục người dùng đã xác nhận/khoá giữ nguyên chữ của người dùng). Mục bị từ chối không hồi sinh.
    - Đã xoá mềm → bỏ qua (người dùng đã xoá; không hồi sinh, không vướng unique).
    """
    if type_ not in TYPES:
        raise ValidationFailed(f"Loại mục Story Bible không hợp lệ: {type_}.")
    key = T.slug_key(key or summary, default=type_)
    item = find(s, project_id, type_, key)
    action = "evidence"
    if item is None:
        item = BibleItem(project_id=project_id, type=type_, key=key, summary=summary or "",
                         attributes=dict(attributes or {}), status=status, locked=False, story_order=story_order,
                         asset_id=asset_id)
        s.add(item)
        s.flush()
        action = "created"
    elif item.deleted_at is not None:
        return item, "skipped"
    elif not item.locked and item.status == "pending":
        if summary and summary != item.summary:
            item.summary = summary
            action = "updated"
        if attributes:
            item.attributes = {**(item.attributes or {}), **attributes}
        if story_order is not None and item.story_order is None:
            item.story_order = story_order
    for ev in evidence or []:
        add_evidence(s, item, ev.get("quote", ""), source=ev.get("source") or "script",
                     sequence_id=ev.get("sequence_id"), start=ev.get("start"), end=ev.get("end"))
    reindex(s, item)
    return item, action
