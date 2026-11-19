"""Hộp chứa dịch vụ dùng chung của ứng dụng (gắn vào `app.state.services`).

Các task sau bổ sung trường: bus, worker, providers, resolver.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sagareel.config import AppSettings
from sagareel.core.resolver import Resolver, default_resolver
from sagareel.core.events import EventBus
from sagareel.db.session import Database


@dataclass
class Services:
    settings: AppSettings
    boot_id: str
    db: Database
    resolver: Resolver = field(default_factory=default_resolver)
    bus: EventBus = field(default_factory=EventBus, kw_only=True)
