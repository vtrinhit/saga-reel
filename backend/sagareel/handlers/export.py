"""Job `export.package`: dựng + đóng gói một lô xuất (tạo bởi op `export.create`, logic ở `services/export/package.py`)."""

from __future__ import annotations

from sagareel.services import jobs
from sagareel.services.export import package


@jobs.job_handler(package.JOB_TYPE, resumable=True)
async def export_package(run) -> dict:
    """`params`: `{export_ids: [int], batch: str}`. Gói đã `done` được bỏ qua khi chạy lại (nối lại sau khi tắt app)."""
    return await package.run_batch(run.services, run, run.params["export_ids"], run.params["batch"])
