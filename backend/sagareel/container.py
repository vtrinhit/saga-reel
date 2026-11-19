"""Hộp chứa dịch vụ dùng chung của ứng dụng (gắn vào `app.state.services`).

Các task sau bổ sung trường: db, bus, worker, providers, resolver.
"""

from __future__ import annotations

from dataclasses import dataclass

from sagareel.config import AppSettings


@dataclass
class Services:
    settings: AppSettings
    boot_id: str
