"""Kết nối provider và định tuyến LLM theo công việc."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Mapped, mapped_column

from sagareel.db.base import Base, JSONDict, JSONList, Tracked


def _default_route() -> dict[str, Any]:
    return {"connection": None, "model": None, "params": {}}


class ProviderConnection(Tracked, Base):
    __tablename__ = "provider_connection"

    kind: Mapped[str]  # image/video/llm/tts/stt
    adapter: Mapped[str]
    name: Mapped[str] = mapped_column(unique=True)
    base_url: Mapped[str] = mapped_column(default="")
    # Bí mật: không bao giờ in/log/trả về; serializer che thành "••••" + 4 ký tự cuối.
    api_key: Mapped[str] = mapped_column(default="", info={"secret": True})
    default_model: Mapped[str] = mapped_column(default="")
    options: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)
    concurrency: Mapped[int] = mapped_column(default=2)
    enabled: Mapped[bool] = mapped_column(default=True)
    health: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)


class LlmTask(Tracked, Base):
    __tablename__ = "llm_task"

    key: Mapped[str] = mapped_column(unique=True)
    label: Mapped[str]
    description: Mapped[str] = mapped_column(default="")
    template_key: Mapped[str] = mapped_column(default="")
    needs_vision: Mapped[bool] = mapped_column(default=False)
    json_mode: Mapped[bool] = mapped_column(default=False)
    route: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=_default_route)
    fallbacks: Mapped[list[Any]] = mapped_column(JSONList, default=list)
