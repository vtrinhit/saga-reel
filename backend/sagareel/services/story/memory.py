"""Bộ nhớ truyện dài (spec §15): ngữ cảnh khi viết tập N trong ngân sách token.

Ngữ cảnh gồm (theo thứ tự ưu tiên khi phải cắt — phần sau bị cắt trước):
1. Bible lõi: mục đã khoá ("nhãn nhân thiết") và nhân vật đã xác nhận.
2. Tóm tắt các tập gần nhất (`RECENT_FULL` tập, đầy đủ).
3. Mục Story Bible liên quan tìm bằng FTS theo nhân vật/từ khoá của dàn ý tập N (cả `pending`), kèm một trích dẫn.
4. Câu hỏi mở chưa giải.
5. Tóm tắt nén theo arc của các tập cũ hơn (synopsis của nhóm arc nếu có, không thì gộp mỗi lô 10 tập).
6. Phần còn lại của bible đã xác nhận (bối cảnh, quan hệ, tuyến truyện, sự kiện, sự thật).

Ngân sách: `story.context_budget_tokens` (mặc định 12000, ước 4 ký tự/token) cho **toàn bộ biến** gửi LLM; phần ngữ cảnh
nhận phần còn lại sau các biến khác. Tóm tắt một tập lấy từ `sequence.pipeline.memory.summary` (do
`story.memory_summarize` ghi), không có thì `synopsis`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from sagareel.db.models import BibleEvidence, BibleItem, GroupNode, Project, Sequence
from sagareel.services.story import bible
from sagareel.services.story import text as T

RECENT_FULL = 3
ARC_BLOCK = 10
EPISODE_SUMMARY_CHARS = 700
ARC_EPISODE_CHARS = 140
ARC_CHARS = 1200
ITEM_CHARS = 400
QUOTE_CHARS = 200
FTS_LIMIT = 40
CORE_TYPES = ("character",)
TYPE_LABEL = {"character": "Nhân vật", "location": "Bối cảnh", "relationship": "Quan hệ", "event": "Sự kiện",
              "storyline": "Tuyến truyện", "fact": "Sự thật", "open_question": "Câu hỏi mở"}


@dataclass
class Section:
    key: str
    title: str
    lines: list[str] = field(default_factory=list)
    ids: list[int] = field(default_factory=list)  # id mục bible tương ứng từng dòng (None nếu không phải mục bible)


@dataclass
class Packed:
    text: str
    tokens: int
    budget_tokens: int
    sections: dict[str, int]  # số dòng giữ lại theo phần
    dropped: dict[str, int]  # số dòng bị cắt theo phần
    item_ids: list[int]

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "tokens": self.tokens, "budget_tokens": self.budget_tokens,
                "sections": dict(self.sections), "dropped": dict(self.dropped), "item_ids": list(self.item_ids)}


def episode_summary(seq: Sequence) -> str:
    mem = (seq.pipeline or {}).get("memory") or {}
    return str(mem.get("summary") or seq.synopsis or "").strip()


def _item_line(item: BibleItem, quote: str | None = None) -> str:
    label = TYPE_LABEL.get(item.type, item.type)
    traits = (item.attributes or {}).get("traits")
    extra = ""
    if isinstance(traits, list) and traits:
        extra = " | nhân thiết: " + ", ".join(str(t) for t in traits)
    when = f" (tập {item.story_order})" if item.story_order else ""
    lock = " [KHOÁ]" if item.locked else ""
    line = T.clip_text(f"- {label} `{item.key}`{lock}{when}: {item.summary}{extra}", ITEM_CHARS)
    if quote:
        line += f"\n  > {T.clip_text(quote, QUOTE_CHARS)}"
    return line


def _first_quotes(s: Session, ids: list[int]) -> dict[int, str]:
    if not ids:
        return {}
    out: dict[int, str] = {}
    for e in s.scalars(select(BibleEvidence).where(BibleEvidence.item_id.in_(ids)).order_by(BibleEvidence.id)):
        out.setdefault(e.item_id, e.quote)
    return out


def _arc_lines(s: Session, older: list[Sequence]) -> list[str]:
    """Tóm tắt nén theo arc cho các tập cũ (đã sắp theo số tập)."""
    if not older:
        return []
    groups: dict[Any, list[Sequence]] = {}
    for q in older:
        gid = q.group_id if q.group_id is not None else f"block{(q.number - 1) // ARC_BLOCK}"
        groups.setdefault(gid, []).append(q)
    names = {}
    gids = [g for g in groups if isinstance(g, int)]
    if gids:
        names = {g.id: g for g in s.scalars(select(GroupNode).where(GroupNode.id.in_(gids)))}
    lines = []
    for gid, eps in groups.items():
        lo, hi = eps[0].number, eps[-1].number
        node = names.get(gid) if isinstance(gid, int) else None
        title = f"Arc «{node.title}»" if node is not None else "Các tập"
        if node is not None and (node.synopsis or "").strip():
            body = node.synopsis.strip()
        else:
            body = " ".join(f"[{q.number}] {T.clip_text(episode_summary(q), ARC_EPISODE_CHARS)}"
                            for q in eps if episode_summary(q))
        if body:
            lines.append(T.clip_text(f"- {title} (tập {lo}–{hi}): {body}", ARC_CHARS))
    return lines


def build(s: Session, project: Project, number: int, query_text: str, budget_chars: int, *,
          exclude_sequence_id: int | None = None) -> Packed:
    """Ngữ cảnh viết tập số `number` của `project` cho truy vấn `query_text` (dàn ý/nhân vật), ≤ `budget_chars`."""
    items = s.scalars(select(BibleItem).where(
        BibleItem.project_id == project.id, BibleItem.deleted_at.is_(None), BibleItem.status != "rejected",
        (BibleItem.story_order.is_(None)) | (BibleItem.story_order <= number)).order_by(BibleItem.id)).all()
    core = Section("bible_core", "Story Bible — nhân vật và điều đã khoá")
    rest = Section("bible", "Story Bible — đã xác nhận")
    openq = Section("open_questions", "Câu hỏi còn mở")
    used: set[int] = set()
    for it in sorted(items, key=lambda i: (not i.locked, i.type != "character", i.id)):
        if it.type == "open_question":
            if not (it.attributes or {}).get("resolved"):
                openq.lines.append(_item_line(it))
                openq.ids.append(it.id)
                used.add(it.id)
            continue
        if it.locked or (it.status == "confirmed" and it.type in CORE_TYPES):
            core.lines.append(_item_line(it))
            core.ids.append(it.id)
            used.add(it.id)
    # mục liên quan theo FTS (từ khoá dàn ý + tên nhân vật), kể cả pending
    hits = Section("relevant", "Liên quan tới tập này (tìm trong bộ nhớ truyện)")
    kws = T.keywords(query_text)
    if kws:
        found = bible.search_ids(s, project.id, " ".join(kws), mode="or", limit=FTS_LIMIT, max_order=number)
        ids = [i for i, _ in found if i not in used]
        quotes = _first_quotes(s, ids)
        for i in ids:
            it = s.get(BibleItem, i)
            hits.lines.append(_item_line(it, quotes.get(i)))
            hits.ids.append(i)
            used.add(i)
    for it in sorted(items, key=lambda i: (-(i.story_order or 0), i.id)):
        if it.id in used or it.status != "confirmed" or it.type == "open_question":
            continue
        rest.lines.append(_item_line(it))
        rest.ids.append(it.id)
    # tập trước
    seqs = s.scalars(select(Sequence).where(Sequence.project_id == project.id, Sequence.deleted_at.is_(None),
                                            Sequence.number < number).order_by(Sequence.number, Sequence.id)).all()
    seqs = [q for q in seqs if q.id != exclude_sequence_id]
    recent_seqs = seqs[-RECENT_FULL:]
    older = seqs[: max(0, len(seqs) - RECENT_FULL)]
    recent = Section("recent", "Các tập ngay trước")
    for q in recent_seqs:
        summ = episode_summary(q)
        if summ:
            recent.lines.append(T.clip_text(f"- Tập {q.number} «{q.title}»: {summ}", EPISODE_SUMMARY_CHARS))
            recent.ids.append(0)
    arcs = Section("arcs", "Tóm tắt các tập trước theo arc")
    arcs.lines = _arc_lines(s, older)
    arcs.ids = [0] * len(arcs.lines)
    return pack([core, recent, hits, openq, arcs, rest], budget_chars)


def pack(sections: list[Section], budget_chars: int) -> Packed:
    """Ghép các phần theo thứ tự ưu tiên; dòng không còn vừa ngân sách bị bỏ (đếm vào `dropped`)."""
    budget_chars = max(0, int(budget_chars))
    kept: dict[str, list[str]] = {}
    dropped: dict[str, int] = {}
    item_ids: list[int] = []
    total = 0
    for sec in sections:
        header = f"## {sec.title}\n"
        for line, iid in zip(sec.lines, sec.ids or [0] * len(sec.lines), strict=False):
            cost = len(line) + 1 + (len(header) + 1 if sec.key not in kept else 0)
            if total + cost > budget_chars:
                dropped[sec.key] = dropped.get(sec.key, 0) + 1
                continue
            kept.setdefault(sec.key, []).append(line)
            total += cost
            if iid:
                item_ids.append(iid)
    order = [sec for sec in sections if sec.key in kept]
    text = "\n\n".join(f"## {sec.title}\n" + "\n".join(kept[sec.key]) for sec in order)
    return Packed(text=text, tokens=T.tokens(text), budget_tokens=budget_chars // T.CHARS_PER_TOKEN,
                  sections={k: len(v) for k, v in kept.items()}, dropped=dropped, item_ids=item_ids)
