"""Adapter giả `fake_*`: chạy cục bộ, không mạng, không tốn tiền — cho test tự động, e2e dài và chạy thử.

Mọi adapter giả nhận `options.health` ∈ {alive, dead, not_configured, unknown} để giả lập kết quả kiểm tra sức khoẻ.
Ngân sách không tính adapter có id bắt đầu bằng `fake_` (`services.budget`).
"""

from __future__ import annotations

from sagareel.providers.base import Adapter, ConfigField, Health

FAKE_PREFIX = "fake_"
ASPECTS = ["9:16", "16:9", "1:1", "4:5", "3:4"]

HEALTH_FIELD = ConfigField(
    "health", "Kết quả kiểm tra giả lập", "enum", False, "alive",
    choices=["alive", "dead", "not_configured", "unknown"],
    help="Trạng thái mà nút “Kiểm tra” trả về (để thử giao diện).",
)

_HEALTH_DETAIL = {
    "alive": "Adapter giả luôn sẵn sàng.",
    "dead": "Adapter giả đang giả lập lỗi kết nối.",
    "not_configured": "Adapter giả đang giả lập thiếu cấu hình.",
    "unknown": "Adapter giả không rõ trạng thái.",
}


def fake_health(adapter: Adapter) -> Health:
    status = adapter.conn.options.get("health", "alive")
    if status not in _HEALTH_DETAIL:
        status = "unknown"
    caps = adapter.capabilities() if status == "alive" else {}
    return Health(status=status, detail=_HEALTH_DETAIL[status], capabilities=caps)


def aspect_size(aspect: str, short_side: int) -> tuple[int, int]:
    """(rộng, cao) chẵn theo tỉ lệ `"W:H"`, cạnh ngắn = `short_side`."""
    try:
        w_s, h_s = aspect.split(":")
        rw, rh = float(w_s), float(h_s)
        if rw <= 0 or rh <= 0:
            raise ValueError
    except ValueError:
        rw, rh = 9.0, 16.0
    short = max(16, int(short_side))
    if rw <= rh:
        w, h = short, short * rh / rw
    else:
        w, h = short * rw / rh, short
    return round(w / 2) * 2, round(h / 2) * 2
