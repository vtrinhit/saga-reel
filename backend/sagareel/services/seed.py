"""Seed dữ liệu mặc định và kết nối provider từ `.env.local` (lần chạy đầu).

- `seed_defaults(db)` — chạy mỗi lần khởi động, idempotent: chèn `llm_task` còn thiếu theo `key` (kể cả key thuộc
  dòng đã xoá mềm thì coi là "đã có" — không hồi sinh, không vướng unique). Công việc LLM đã có không bị đổi.
- `seed_samples(db)` — dự án mẫu còn thiếu (`services/samples.seed`) + chép prompt mẫu một lần cho dự án cũ chưa có
  prompt (`samples.backfill_once`). Prompt là dữ liệu của project, không có bộ template chung.
- `seed_from_env(db, env_file)` — chỉ chạy khi `system_state["seeded_env_at"]` chưa có (`force=True` bỏ qua cờ, dùng
  cho op `provider.import_env`). Không có file → không làm gì, không đặt cờ (file thêm sau thì lần khởi động sau seed).
  Tạo kết nối chưa có theo tên (tên đã có — kể cả đã xoá mềm — thì bỏ qua):

  | tên         | adapter         | cần khoá env                | ghi chú                                        |
  |-------------|-----------------|-----------------------------|------------------------------------------------|
  | `maivideo`  | `maivideo`      | `MAIVIDEO_API_KEY`          | concurrency 2, options `{"timeout_s": 1800}`   |
  | `groktele`  | `multikit`      | `MULTIKIT_URL`              | concurrency 4, base_url = `MULTIKIT_URL`       |
  | `llm-local` | `openai_compat` | `LLM_BASE_URL`, `LLM_MODEL` | concurrency 4, default_model = `LLM_MODEL`, vision |
  | `edge-tts`  | `edge_tts`      | —                           | concurrency 2                                  |

  Cài đặt toàn cục (chỉ khoá **chưa** đặt ở tầng global; giá trị schema không nhận → bỏ qua kèm một dòng log không có
  giá trị): `image.model` ← `MAIVIDEO_MODEL`, `video.resolution` ← `MULTIKIT_RESOLUTION`, `video.duration` ←
  `MULTIKIT_DURATION`. Công việc LLM chưa có route
  (`route.connection` trống) được định tuyến tới `llm-local` (model trống → dùng `default_model` của kết nối).

Mọi ghi đi qua một `UnitOfWork` (actor `system:seed`) nên hiện trong lịch sử; cột `api_key` bị che trong revision.
Không bao giờ log hay trả về giá trị đọc từ file env. Không gọi provider nào.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from sagareel.core.errors import AppError
from sagareel.core.ops import current_op
from sagareel.core.resolver import Resolver, default_resolver
from sagareel.core.uow import UnitOfWork
from sagareel.db.base import utcnow
from sagareel.db.models import GlobalSettings, LlmTask, ProviderConnection
from sagareel.db.session import Database
from sagareel.services import llm, system_state
from sagareel.services.settings import GLOBAL_ID

logger = logging.getLogger("sagareel.seed")

SEED_ACTOR = "system:seed"
SEEDED_ENV_KEY = "seeded_env_at"
OP_SEED_DEFAULTS = "seed.defaults"
OP_SEED_ENV = "seed.env"
LLM_CONNECTION = "llm-local"

# (khoá cài đặt, biến env) — chỉ đặt khi biến có giá trị và schema nhận giá trị đó.
_ENV_SETTINGS = (
    ("image.model", "MAIVIDEO_MODEL"),
    ("video.resolution", "MULTIKIT_RESOLUTION"),
    ("video.duration", "MULTIKIT_DURATION"),
)
_INLINE_COMMENT = re.compile(r"\s+#")


# --- Đọc file env ---------------------------------------------------------------------------------------------------


def parse_env(text: str) -> dict[str, str]:
    """`KEY=VALUE` mỗi dòng; bỏ dòng trống/`#`, tiền tố `export `, cặp nháy bao ngoài. Giá trị không có nháy: bỏ chú
    thích cuối dòng (`␣#…`). Giá trị rỗng bị bỏ."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if value[:1] in ("'", '"'):
            end = value.find(value[0], 1)
            value = value[1:end] if end > 0 else value[1:]
        else:
            value = _INLINE_COMMENT.split(value, 1)[0].strip()
        if key and value:
            out[key] = value
    return out


def read_env(env_file: Path) -> dict[str, str] | None:
    """Nội dung file env đã parse; không có file → `None`."""
    path = Path(env_file)
    if not path.is_file():
        return None
    return parse_env(path.read_text(encoding="utf-8-sig"))


# --- Kế hoạch từ env --------------------------------------------------------------------------------------------------


@dataclass
class EnvPlan:
    connections: list[dict[str, Any]]  # giá trị cột của kết nối (có bí mật — chỉ dùng nội bộ)
    settings: dict[str, Any]


@dataclass
class EnvResult:
    """Kết quả (hoặc dự kiến, khi dry-run) của một lần seed từ env — chỉ tên/khoá, không có giá trị bí mật."""

    connections: list[str] = field(default_factory=list)
    settings: list[str] = field(default_factory=list)
    routed_tasks: list[str] = field(default_factory=list)
    ran: bool = False  # False khi bị cờ `seeded_env_at` chặn

    def to_dict(self) -> dict[str, Any]:
        return {"connections": list(self.connections), "settings": list(self.settings),
                "routed_tasks": list(self.routed_tasks)}


def _connection_specs(env: dict[str, str]) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []

    def need(name: str, *keys: str) -> bool:
        missing = [k for k in keys if not env.get(k)]
        if missing:
            logger.info("Seed: bỏ qua kết nối %s (thiếu %s trong file env)", name, ", ".join(missing))
        return not missing

    if need("maivideo", "MAIVIDEO_API_KEY"):
        model = env.get("MAIVIDEO_MODEL", "")
        specs.append({"name": "maivideo", "kind": "image", "adapter": "maivideo", "base_url": env.get("MAIVIDEO_BASE_URL", ""),
                          "api_key": env["MAIVIDEO_API_KEY"], "default_model": model, "options": {"timeout_s": 1800},
                          "concurrency": 2})
    if need("groktele", "MULTIKIT_URL"):
        specs.append({"name": "groktele", "kind": "video", "adapter": "multikit", "base_url": env["MULTIKIT_URL"],
                          "api_key": env.get("MULTIKIT_API_KEY", ""), "default_model": "", "options": {}, "concurrency": 4})
    if need(LLM_CONNECTION, "LLM_BASE_URL", "LLM_MODEL"):
        specs.append({"name": LLM_CONNECTION, "kind": "llm", "adapter": "openai_compat", "base_url": env["LLM_BASE_URL"],
                          "api_key": env.get("LLM_API_KEY", ""), "default_model": env["LLM_MODEL"],
                          "options": {"vision": True}, "concurrency": 4})
    # Adapter `edge_tts` không cần khoá/địa chỉ.
    specs.append({"name": "edge-tts", "kind": "tts", "adapter": "edge_tts", "base_url": "", "api_key": "", "default_model": "",
                      "options": {}, "concurrency": 2})
    return specs


def _coerce(resolver: Resolver, key: str, raw: str) -> Any:
    d = resolver.definition(key)
    if d.type == "int" or (d.type == "enum" and d.choices and all(isinstance(c, int) for c in d.choices)):
        return int(raw)
    return raw


def _settings_from_env(env: dict[str, str], resolver: Resolver) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key, var in _ENV_SETTINGS:
        raw = env.get(var)
        if raw is None:
            continue
        try:
            value = _coerce(resolver, key, raw)
            resolver.validate_values({key: value})
        except (ValueError, AppError):
            logger.warning("Seed: bỏ qua cài đặt %s từ %s (giá trị không hợp lệ theo settings schema)", key, var)
            continue
        values[key] = value
    return values


def plan_env(env: dict[str, str], resolver: Resolver) -> EnvPlan:
    return EnvPlan(connections=_connection_specs(env), settings=_settings_from_env(env, resolver))


# --- Áp dụng ----------------------------------------------------------------------------------------------------------


def _apply_env(s: Session, u: UnitOfWork | None, plan: EnvPlan) -> EnvResult:
    """Tính (và nếu có `u` thì ghi) phần còn thiếu của `plan`. `u is None` → chỉ đọc (dry-run)."""
    result = EnvResult(ran=True)
    rows = s.scalars(select(ProviderConnection)).all()
    taken = {r.name for r in rows}  # gồm cả kết nối đã xoá mềm (tên unique)
    live = {r.name for r in rows if r.deleted_at is None}
    to_create = [c for c in plan.connections if c["name"] not in taken]
    result.connections = [c["name"] for c in to_create]

    g = s.get(GlobalSettings, GLOBAL_ID)
    current = dict(g.data) if g is not None else {}
    new_settings = {k: v for k, v in plan.settings.items() if k not in current}
    result.settings = sorted(new_settings)

    tasks: list[LlmTask] = []
    if LLM_CONNECTION in live or LLM_CONNECTION in result.connections:
        tasks = [t for t in s.scalars(select(LlmTask).where(LlmTask.deleted_at.is_(None)).order_by(LlmTask.id)).all()
                 if llm.normalize_route(t.route)["connection"] is None]
    result.routed_tasks = [t.key for t in tasks]

    if u is None:
        return result

    for spec in to_create:
        s.add(ProviderConnection(**spec, enabled=True, health={}))
    if new_settings:
        if g is None:
            # Như settings.set_global: tạo dòng rỗng trước khi theo dõi để hoàn tác chỉ làm trống dữ liệu.
            g = GlobalSettings(id=GLOBAL_ID, data={})
            s.add(g)
            s.flush()
        u.track(g)
        g.data = {**current, **new_settings}
    for t in tasks:
        t.route = {**llm.normalize_route(t.route), "connection": LLM_CONNECTION}
    system_state.set(s, SEEDED_ENV_KEY, utcnow().isoformat())
    return result


def import_env(
    db: Database,
    env_file: Path,
    *,
    force: bool = False,
    dry_run: bool = False,
    actor: str = SEED_ACTOR,
    resolver: Resolver | None = None,
) -> EnvResult | None:
    """Seed từ file env. Không có file → `None`. Cờ `seeded_env_at` đã có và không `force` → `EnvResult(ran=False)`.

    `dry_run=True`: không ghi gì, trả những gì sẽ được tạo/đặt.
    """
    env = read_env(env_file)
    if env is None:
        return None
    if not force:  # đã seed → khỏi lấy khoá ghi (kiểm lại dưới khoá bên dưới)
        with db.session() as s:
            if system_state.get(s, SEEDED_ENV_KEY) is not None:
                return EnvResult()
    plan = plan_env(env, resolver or default_resolver())  # đọc/chuẩn bị ngoài khoá ghi

    if dry_run:
        with db.session() as s:
            return _apply_env(s, None, plan)

    u = UnitOfWork(db, actor=actor, op_name=current_op.get() or OP_SEED_ENV, project_id=None,
                   summary="Nạp kết nối provider và cài đặt từ .env.local")
    with u as s:
        if not force and system_state.get(s, SEEDED_ENV_KEY) is not None:
            return EnvResult()
        result = _apply_env(s, u, plan)
    if result.connections:
        logger.info("Seed: đã tạo kết nối %s", ", ".join(result.connections))
    return result


def seed_from_env(db: Database, env_file: Path, *, force: bool = False, resolver: Resolver | None = None) -> list[str]:
    """Seed kết nối provider + cài đặt từ `env_file` một lần; trả tên kết nối đã tạo (`[]` nếu không làm gì)."""
    result = import_env(db, env_file, force=force, resolver=resolver)
    return [] if result is None else list(result.connections)


# --- Dữ liệu mặc định -------------------------------------------------------------------------------------------------


def seed_defaults(db: Database) -> None:
    """Chèn công việc LLM mặc định còn thiếu. Idempotent. (Prompt là dữ liệu của project — xem `seed_samples`.)"""
    tasks = llm.default_tasks()

    u = UnitOfWork(db, actor=SEED_ACTOR, op_name=OP_SEED_DEFAULTS, project_id=None,
                   summary="Nạp công việc LLM mặc định")
    with u as s:
        task_keys = set(s.scalars(select(LlmTask.key)).all())  # gồm cả dòng đã xoá mềm
        for t in tasks:
            if t["key"] not in task_keys:
                s.add(LlmTask(**t))

    if u.change_set_id is not None:
        logger.info("Seed: đã thêm %d công việc LLM mặc định", len(u.changed))


def seed_samples(db: Database) -> None:
    """Dự án mẫu còn thiếu + chép prompt mẫu (một lần) cho dự án cũ chưa có prompt. Idempotent."""
    from sagareel.services import samples

    samples.seed(db)
    samples.backfill_once(db)
