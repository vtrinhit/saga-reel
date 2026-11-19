"""Mã xác nhận cho thao tác phá huỷ hai bước (`*.delete_preview` → `*.delete(confirm_token)`).

Token = `"<hết hạn unix>.<hmac>"`, với `hmac = HMAC-SHA256(app_secret, f"{op}:{ids}:{impact_hash}:{hết hạn}")[:24]`.
Hết hạn sau `TTL_S` (10 phút). `impact_hash` băm bản mô tả tác động: tác động đổi giữa lúc xem trước và lúc xoá
(vd. thêm job đang chờ) thì token cũ hết hiệu lực. Khoá HMAC là `system_state.app_secret` (không bao giờ trả qua API).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Iterable
from typing import Any

from sqlalchemy.orm import Session

from sagareel.core.errors import ConfirmRequired
from sagareel.services import system_state

TTL_S = 600


def impact_hash(impact: Any) -> str:
    raw = json.dumps(impact, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _ids(ids: Iterable[int]) -> str:
    return ",".join(str(i) for i in sorted(set(ids)))


def _mac(secret: str, op: str, ids: Iterable[int], impact: Any, expires: int) -> str:
    msg = f"{op}:{_ids(ids)}:{impact_hash(impact)}:{expires}"
    return hmac.new(secret.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()[:24]


def make_token(session: Session, op: str, ids: Iterable[int], impact: Any) -> str:
    """Token cho `op` (tên op phá huỷ, vd. `provider.delete`) trên `ids` với tác động `impact`. Người gọi commit
    (lần đầu có thể sinh `app_secret`)."""
    expires = int(time.time()) + TTL_S
    return f"{expires}.{_mac(system_state.app_secret(session), op, ids, impact, expires)}"


def verify(session: Session, op: str, ids: Iterable[int], impact: Any, token: str | None) -> None:
    """Thiếu/sai/hết hạn → `ConfirmRequired` (`confirm_required`, 409)."""
    preview = f"{op}_preview" if not op.endswith(".delete") else op.rsplit(".", 1)[0] + ".delete_preview"
    hint = f"Gọi {preview} để xem tác động và lấy confirm_token mới (hiệu lực {TTL_S // 60} phút)."
    if not token:
        raise ConfirmRequired("Thao tác này cần xác nhận bằng confirm_token.", hint=hint)
    exp_s, _, mac = token.partition(".")
    try:
        expires = int(exp_s)
    except ValueError:
        raise ConfirmRequired("confirm_token không hợp lệ.", hint=hint) from None
    expected = _mac(system_state.app_secret(session), op, ids, impact, expires)
    if not hmac.compare_digest(mac, expected):
        raise ConfirmRequired("confirm_token không hợp lệ hoặc tác động đã thay đổi kể từ lúc xem trước.", hint=hint)
    if time.time() > expires:
        raise ConfirmRequired("confirm_token đã hết hạn.", hint=hint)
