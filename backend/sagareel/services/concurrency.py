"""Luồng: trần số việc chạy cùng lúc theo **loại provider** trên toàn app (`concurrency.<kind>`).

- Loại: `image`, `video`, `llm`, `tts`, `stt` (theo `provider_connection.kind`) và `render` (lane `ffmpeg`).
- Trần loại là **trần cứng** cộng mọi lane cùng loại; `concurrency` của từng kết nối (lane `conn:<id>`) vẫn là trần
  con. Worker (`services/worker.py`) đọc trần mới mỗi nhịp điều phối — không có cache — nên đổi là việc mới dùng ngay;
  hạ trần không huỷ việc đang chạy, chỉ ngừng khởi chạy thêm tới khi số đang chạy xuống dưới trần.
- Lane nội bộ gọi provider trong handler: `story` (viết truyện, dịch lồng tiếng — gọi LLM trực tiếp) tính vào `llm`.
  Lane `pipeline` chỉ điều phối (đưa job con vào hàng đợi), không tính vào loại nào.
- Đổi trần (qua `settings.set_global` hoặc `queue.set_concurrency`) hay sửa kết nối → `notify()`: đánh thức worker và
  phát `queue.updated`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sagareel.core.context import OpContext
from sagareel.db.models import Job, ProviderConnection
from sagareel.services.settings import global_layer

if TYPE_CHECKING:
    from sagareel.container import Services

KINDS: tuple[str, ...] = ("image", "video", "llm", "tts", "stt", "render")
LABELS = {"image": "Ảnh", "video": "Video", "llm": "LLM", "tts": "Giọng đọc", "stt": "Nhận dạng giọng",
          "render": "Dựng phim"}
SETTING_PREFIX = "concurrency."
MIN_CAP, MAX_CAP = 1, 20
CONN_PREFIX = "conn:"
# Lane không phải kết nối provider nhưng tính vào một loại.
INTERNAL_LANES: dict[str, str] = {"ffmpeg": "render", "story": "llm"}
INTERNAL_LABELS = {"ffmpeg": "Dựng phim / ffmpeg", "story": "Viết truyện / dịch lồng tiếng"}
EVENT = "queue.updated"
# Khoá cài đặt mà đổi thì trần lane/loại đổi theo (lane `ffmpeg`/`story` dùng luôn trần loại).
WATCHED_KEYS = tuple(f"{SETTING_PREFIX}{k}" for k in KINDS)


def setting_key(kind: str) -> str:
    return f"{SETTING_PREFIX}{kind}"


def clamp(value: Any, default: int) -> int:
    """Ép về số nguyên trong [1, 20]; giá trị hỏng → `default` (đã ép)."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = int(default)
    return max(MIN_CAP, min(MAX_CAP, n))


def caps(services: Services, session: Session) -> dict[str, int]:
    """Trần hiệu lực từng loại (mặc định + tầng global), đã ép 1..20."""
    layer = global_layer(session)
    out: dict[str, int] = {}
    for kind in KINDS:
        key = setting_key(kind)
        default = services.resolver.definition(key).default
        out[kind] = clamp(services.resolver.resolve(key, [layer]).value, default)
    return out


def _conn_id(lane: str) -> int | None:
    raw = lane[len(CONN_PREFIX):] if lane.startswith(CONN_PREFIX) else ""
    return int(raw) if raw.isdigit() else None


def lane_kinds(session: Session, lanes: set[str] | list[str]) -> dict[str, str]:
    """Lane → loại cho các lane thuộc một loại (lane khác vắng mặt). Kết nối đã xoá mềm vẫn tính (việc của nó có thể
    còn đang chạy)."""
    out = {lane: INTERNAL_LANES[lane] for lane in lanes if lane in INTERNAL_LANES}
    ids = {cid: lane for lane in lanes if (cid := _conn_id(lane)) is not None}
    if ids:
        for cid, kind in session.execute(
            select(ProviderConnection.id, ProviderConnection.kind).where(ProviderConnection.id.in_(ids))
        ):
            if kind in KINDS:
                out[ids[cid]] = kind
    return out


def plan(services: Services, lanes: set[str]) -> tuple[dict[str, str], dict[str, int]]:
    """(lane → loại, loại → trần) cho một nhịp điều phối, đọc mới từ DB."""
    with services.db.session() as s:
        return lane_kinds(s, lanes), caps(services, s)


def notify(services: Services) -> None:
    """Trần/kết nối vừa đổi: đánh thức worker (việc đang chờ chạy ngay nếu còn chỗ) và phát `queue.updated`."""
    worker = getattr(services, "worker", None)
    if worker is not None:
        worker.wake()
    services.bus.publish(EVENT, {"reason": "concurrency"})


def _lane_cap(services: Services, lane: str) -> int:
    providers = getattr(services, "providers", None)
    if providers is not None:
        return providers.lane_limit(lane)
    return services.lane_limit(lane)


def overview(services: Services) -> dict[str, Any]:
    """Mỗi loại: trần, số việc đang chạy/đang chờ (từ DB) và các lane con (kết nối + lane nội bộ) với trần riêng."""
    with services.db.session() as s:
        kind_caps = caps(services, s)
        counts: dict[str, dict[str, int]] = {}
        for lane, status, n in s.execute(
            select(Job.lane, Job.status, func.count()).where(Job.status.in_(("queued", "running")))
            .group_by(Job.lane, Job.status)
        ):
            counts.setdefault(lane, {"queued": 0, "running": 0})[status] = int(n)
        conns = list(s.scalars(select(ProviderConnection).order_by(ProviderConnection.kind, ProviderConnection.name)))
        kinds_of = lane_kinds(s, set(counts))
    kinds: dict[str, dict[str, Any]] = {
        k: {"kind": k, "label": LABELS[k], "key": setting_key(k), "cap": kind_caps[k], "running": 0, "queued": 0,
            "connections": []}
        for k in KINDS
    }
    for lane, c in counts.items():
        kind = kinds_of.get(lane)
        if kind is not None:
            kinds[kind]["running"] += c["running"]
            kinds[kind]["queued"] += c["queued"]
    for row in conns:
        if row.kind not in kinds or row.deleted_at is not None:
            continue
        lane = f"{CONN_PREFIX}{row.id}"
        c = counts.get(lane, {})
        kinds[row.kind]["connections"].append({
            "lane": lane, "connection_id": row.id, "name": row.name, "enabled": bool(row.enabled),
            "cap": max(1, int(row.concurrency)), "running": c.get("running", 0), "queued": c.get("queued", 0),
        })
    for lane, kind in INTERNAL_LANES.items():
        c = counts.get(lane, {})
        kinds[kind]["connections"].append({
            "lane": lane, "connection_id": None, "name": INTERNAL_LABELS[lane], "enabled": True,
            "cap": _lane_cap(services, lane), "running": c.get("running", 0), "queued": c.get("queued", 0),
        })
    worker = getattr(services, "worker", None)
    return {"kinds": [kinds[k] for k in KINDS], "min": MIN_CAP, "max": MAX_CAP,
            "paused": bool(worker.paused) if worker is not None else False}


def set_caps(ctx: OpContext, values: dict[str, int]) -> dict[str, Any]:
    """Ghi trần loại vào tầng global (có lịch sử, hoàn tác được) rồi trả overview mới. `set_global` tự `notify`."""
    from sagareel.services import settings

    settings.set_global(ctx, {setting_key(k): clamp(v, v) for k, v in values.items()})
    return overview(ctx.services)
