"""`RunStub`: bản giả tối thiểu của `services.worker.JobRun` cho test adapter (không DB, không worker)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any


class RunStub:
    def __init__(self, workdir: Path) -> None:
        self.job_id = 1
        self.workdir = workdir
        self.external_id: str | None = None
        self.external: dict = {}
        self.saves: list[tuple[str | None, dict]] = []
        self.logs: list[tuple[str, str]] = []
        self.stages: list[str] = []
        self.cancelled = asyncio.Event()

    def preset_external(self, external_id: str | None = None, **state: Any) -> None:
        """Giả lập trạng thái đã lưu từ lần chạy trước (job nối lại)."""
        self.external_id = external_id
        self.external.update(state)

    async def save_external(self, external_id: str | None = None, **state: Any) -> None:
        self.saves.append((external_id, dict(state)))
        self.external = {**self.external, **state}
        if external_id is not None:
            self.external_id = external_id

    def progress(self, pct: float | None = None, stage: str | None = None, eta_s: float | None = None) -> None:
        if stage is not None:
            self.stages.append(stage)

    def log(self, level: str, msg: str) -> None:
        self.logs.append((level, msg))
