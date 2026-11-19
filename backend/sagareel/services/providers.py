"""Dịch vụ kết nối provider: đọc kết nối, dựng adapter, kiểm tra sức khoẻ, lane và giới hạn song song.

- `find()` đọc kết nối (bỏ qua kết nối đã xoá mềm) **bất kể bật/tắt** — dùng cho op (tính lane, ước tính).
- `connection()` / `adapter()` dành cho job: kết nối tắt → `ProviderUnavailable` (job `failed`, `provider_unavailable`).
- `check()` gọi `check_health()` của adapter (có timeout), ghi cột `health` bằng phiên ngắn thường — **không** qua
  UnitOfWork: sức khoẻ là sổ sách máy, không được sinh change set (sẽ chặn hoàn tác các lần sửa kết nối).
- `lane_limit(lane)` chạy trong thread điều phối của worker: không bao giờ ném lỗi.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import anyio
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from sagareel.core.errors import AppError, NotFound
from sagareel.core.secrets import mask
from sagareel.db.models import ProviderConnection
from sagareel.providers import registry
from sagareel.providers.base import Adapter, Connection, Health, ProviderUnavailable
from sagareel.services.jobs import JobFailed, NeedsAttention, TransientError
from sagareel.services.settings import global_layer

if TYPE_CHECKING:
    from sagareel.container import Services

logger = logging.getLogger("sagareel.providers")

LANE_PREFIX = "conn:"
CHECK_TIMEOUT_S = 30.0


def to_connection(row: ProviderConnection) -> Connection:
    return Connection(
        id=row.id, name=row.name, adapter=row.adapter, kind=row.kind, base_url=row.base_url or "",
        api_key=row.api_key or "", default_model=row.default_model or "", options=dict(row.options or {}),
        concurrency=row.concurrency,
    )


def get_row(session: Session, name_or_id: int | str) -> ProviderConnection:
    """Kết nối chưa xoá theo id (int) hoặc tên (str; chuỗi toàn số không khớp tên thì coi là id)."""
    row: ProviderConnection | None = None
    if isinstance(name_or_id, int):
        row = session.get(ProviderConnection, name_or_id)
    else:
        row = session.scalars(
            select(ProviderConnection).where(ProviderConnection.name == name_or_id,
                                             ProviderConnection.deleted_at.is_(None))
        ).first()
        if row is None and name_or_id.isdigit():
            row = session.get(ProviderConnection, int(name_or_id))
    if row is None or row.deleted_at is not None:
        raise NotFound(
            f"Không tìm thấy kết nối provider '{name_or_id}'.",
            code="connection_not_found",
            hint="Gọi provider.list để xem các kết nối hiện có.",
        )
    return row


def redact_secret(text: str, secret: str) -> str:
    """Thay mọi lần xuất hiện của `secret` trong `text` bằng dạng che."""
    return text.replace(secret, mask(secret)) if secret else text


def _redact(text: str, conn: Connection) -> str:
    return redact_secret(text, conn.api_key)


class ProviderService:
    def __init__(self, services: Services) -> None:
        self.services = services
        registry.discover()

    # --- Đọc ----------------------------------------------------------------------------------------------------

    def setting(self, key: str) -> Any:
        """Giá trị hiệu lực của một khoá cài đặt (mặc định + tầng global)."""
        with self.services.db.session() as s:
            layer = global_layer(s)
        return self.services.resolver.resolve(key, [layer]).value

    def find(self, name_or_id: int | str) -> tuple[Connection, bool]:
        """(kết nối, đang bật). Không có/đã xoá → `NotFound`. Không kiểm tra bật/tắt."""
        with self.services.db.session() as s:
            row = get_row(s, name_or_id)
            return to_connection(row), bool(row.enabled)

    def connection(self, name_or_id: int | str) -> Connection:
        """Kết nối để chạy job: đang tắt → `ProviderUnavailable("Kết nối đang tắt")`."""
        conn, enabled = self.find(name_or_id)
        if not enabled:
            raise ProviderUnavailable(
                f"Kết nối đang tắt: {conn.name}.",
                hint="Bật lại kết nối trong Cài đặt → Provider rồi chạy lại job.",
            )
        return conn

    def adapter(self, name_or_id: int | str, http: httpx.AsyncClient | None = None) -> Adapter:
        conn = self.connection(name_or_id)
        return registry.get(conn.adapter)(conn, http)

    # --- Sức khoẻ -----------------------------------------------------------------------------------------------

    async def check(self, name_or_id: int | str) -> Health:
        """Kiểm tra một kết nối; ghi `health` vào DB và phát `provider.health`. Lỗi adapter → `dead`, không ném."""
        conn, enabled = await anyio.to_thread.run_sync(self.find, name_or_id)
        if not enabled:
            health = Health(status="unknown", detail="Kết nối đang tắt; bật lên để kiểm tra.")
        else:
            health = await self._probe(conn)
        await anyio.to_thread.run_sync(self._save_health, conn.id, health)
        self.services.bus.publish("provider.health", {"id": conn.id, "name": conn.name, "health": health.to_dict()})
        return health

    async def _probe(self, conn: Connection) -> Health:
        try:
            adapter = registry.get(conn.adapter)(conn)
            health = await asyncio.wait_for(adapter.check_health(), CHECK_TIMEOUT_S)
        except TimeoutError:
            return Health(status="dead", detail=f"Hết thời gian chờ ({CHECK_TIMEOUT_S:.0f} s) khi kiểm tra kết nối.")
        except (JobFailed, NeedsAttention, TransientError) as e:
            return Health(status="dead", detail=_redact(e.msg, conn))
        except AppError as e:
            return Health(status="dead", detail=_redact(e.detail, conn))
        except Exception as e:  # noqa: BLE001 — adapter lỗi bất ngờ vẫn chỉ là "dead"
            logger.warning("Kiểm tra kết nối #%s (%s) lỗi: %s", conn.id, conn.adapter, type(e).__name__)
            return Health(status="dead", detail=_redact(f"Lỗi khi kiểm tra: {type(e).__name__}: {e}", conn)[:500])
        health.detail = _redact(health.detail, conn)
        return health

    def _save_health(self, connection_id: int, health: Health) -> None:
        with self.services.db.session(immediate=True) as s:
            row = s.get(ProviderConnection, connection_id)
            if row is not None:
                row.health = health.to_dict()

    # --- Lane ---------------------------------------------------------------------------------------------------

    @staticmethod
    def lane(conn: Connection) -> str:
        return f"{LANE_PREFIX}{conn.id}"

    def lane_limit(self, lane: str) -> int:
        """`conn:<id>` → `concurrency` của kết nối (không có/đã xoá → 1, để job chạy và thất bại rõ ràng);
        `ffmpeg` → cài đặt `jobs.ffmpeg_concurrency` (trừ khi `Services.lane_limits` ghi đè); còn lại →
        `Services.lane_limit`. Lỗi bất kỳ → 1 (không chặn lane)."""
        try:
            if lane.startswith(LANE_PREFIX):
                raw_id = lane[len(LANE_PREFIX):]
                if not raw_id.isdigit():
                    return 1
                with self.services.db.session() as s:
                    row = s.get(ProviderConnection, int(raw_id))
                    if row is None or row.deleted_at is not None:
                        return 1
                    return max(1, int(row.concurrency))
            if lane == "ffmpeg" and lane not in self.services.lane_limits:
                return max(1, int(self.setting("jobs.ffmpeg_concurrency")))
            return self.services.lane_limit(lane)
        except Exception:  # noqa: BLE001
            logger.exception("Không đọc được giới hạn song song của lane %r; dùng 1", lane)
            return 1
