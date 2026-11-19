"""Hộp chứa dịch vụ dùng chung của ứng dụng (gắn vào `app.state.services`).

Các task sau bổ sung trường: bus, worker, providers, resolver.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sagareel.config import AppSettings
from sagareel.core.resolver import Resolver, default_resolver
from sagareel.core.events import EventBus
from sagareel.core.security import LoginLimiter, TTLCache
from sagareel.db.session import Database

if TYPE_CHECKING:
    from sagareel.services.providers import ProviderService
    from sagareel.services.worker import Worker


@dataclass
class Services:
    settings: AppSettings
    boot_id: str
    db: Database
    resolver: Resolver = field(default_factory=default_resolver)
    bus: EventBus = field(default_factory=EventBus, kw_only=True)
    # Worker hàng đợi job (`services/worker.py`), dựng trong `create_app` (hoặc fixture test) sau Services.
    worker: Worker | None = field(default=None, kw_only=True)
    # Giới hạn song song theo lane khi chưa có cấu hình kết nối provider (mặc định 2).
    lane_limits: dict[str, int] = field(default_factory=dict, kw_only=True)
    # Kết nối provider (`services/providers.py`), dựng trong `create_app`; worker lấy giới hạn lane từ đây.
    providers: ProviderService | None = field(default=None, kw_only=True)

    def lane_limit(self, lane: str) -> int:
        return self.lane_limits.get(lane, 2)
    # Cấu hình truy cập (`remote.*` + mật khẩu đã đặt chưa) cho middleware bảo mật; TTL 5 s, xoá khi đổi.
    auth_cache: TTLCache = field(default_factory=lambda: TTLCache(5.0), kw_only=True)
    # Chống dò mật khẩu (đăng nhập + đổi mật khẩu) và giới hạn số lần kiểm scrypt đồng thời.
    login_limiter: LoginLimiter = field(default_factory=LoginLimiter, kw_only=True)
