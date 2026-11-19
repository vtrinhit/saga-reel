# W1 — Chỉ dẫn chung cho mọi task (A–F)

Bạn làm một task trong `docs/superpowers/plans/2026-11-20-m1-m8-master.md` (đọc mục Global Constraints, Review Focus và
**task của bạn**). Spec: `docs/superpowers/specs/2026-11-19-sagareel-studio-design.md` (+ phụ lục
`2026-11-20-film-craft-addendum.md` và tài liệu `docs/research/film-craft/*.md` cho số liệu mặc định).

## Nơi làm
- Chỉ trong worktree `D:\MMO\SagaReel_wt\<x>` (nhánh `wt/<x>`). Không sửa `D:\MMO\SagaReel_Studio`. Không push. Commit trên nhánh.
- Test (PowerShell):
  `cd D:\MMO\SagaReel_wt\<x>; $env:PYTHONPATH="D:\MMO\SagaReel_wt\<x>\backend"; $env:PYTHONIOENCODING="utf-8"`
  rồi kiểm `D:\MMO\SagaReel_Studio\.venv\Scripts\python -c "import sagareel;print(sagareel.__file__)"` trỏ vào worktree,
  `D:\MMO\SagaReel_Studio\.venv\Scripts\python -m pytest backend/tests/<file> -q`.
- Cần thư viện Python mới → báo trong báo cáo (người điều phối cài); không tự sửa `.venv`. `openpyxl` đã được cài sẵn.

## Nền có sẵn (W0, đã gộp)
- Bảng M1–M7 trong `backend/sagareel/db/models/*.py` (migration 0004). **Không tạo migration.** Cần cột mới → tạm dùng
  cột JSON `custom`/`settings`/`meta`/`params` sẵn có và ghi đề xuất vào báo cáo.
- `core/rank.py` (between, n_after, rebalance), `services/entities.py` (get/to_dict/patch có stale_write/list_page/
  soft_delete_preview/soft_delete), `services/projects.py`, `ops/project_ops.py`, `ops/entity_ops.py`.
- `handlers/__init__.load_all()` tự import `handlers/<x>.py` → đặt job handler ở đó (`@job_handler` / `provider_job`).
- `compile/contracts.py`: `RefItem`, `CompiledPrompt`, `compile_clip_image`, `compile_clip_motion`, `compile_sheet`.
- Fixture pytest: `project`, `owner_ctx`, `make_clip_tiny(database, project_id=None) -> media_id`, các fixture M0
  (`client`, `services`, `test_job_handlers`, `make_local_client`, …) — đọc `backend/tests/conftest.py`.
- Mẫu viết op/service/job: `ops/provider_ops.py`, `services/provider_jobs.py` (`_paid`, `_store`, try_image/try_video),
  `services/llm.py` (`run_task`), `services/confirm.py`, `services/budget.py`, `media/ffmpeg.py` (hàm chạy ffmpeg duy nhất).

## File dùng chung (được thêm, không sửa phần người khác)
- `backend/sagareel/defaults/settings_schema.yaml`: chỉ **thêm** khoá mới ở cuối file trong một khối có chú thích
  `# --- task <x> ---`.
- `backend/sagareel/defaults/llm_tasks.yaml`: chỉ thêm task mới ở cuối, cùng kiểu chú thích.
- Không sửa `app.py`, `container.py`, `conftest.py` (cần fixture → đặt trong file test của mình hoặc `backend/tests/_fixtures_<x>.py`).

## Phụ thuộc giữa task chạy song song (chưa có trong worktree của bạn)
- Gọi sang module của task khác bằng **import muộn trong hàm** theo đúng tên ở hợp đồng; test của bạn dùng
  `monkeypatch` thay module đó. Không viết bản sao/thay thế của module người khác. Nối thật được kiểm ở task e2e S.
- Tên đã chốt: A `sagareel.compile.compiler` (+ thân `compile/contracts.py`), B `sagareel.services.clips|takes|sequences`,
  D `sagareel.services.audio.{subtitles,timing,mix,voices,dialogue}`, E `sagareel.render.{layout,normalize,still_motion,chunks,concat,pipeline}`,
  F `sagareel.services.export.*`.

## Chất lượng
- TDD (Skill `superpowers:test-driven-development`). Lỗi người dùng thấy: tiếng Việt có dấu; `error_code` snake_case.
- Mọi op mới khai báo `mcp="tool"` nếu là thao tác hay dùng (xem §23.5), `read_only`, `destructive`, `costly`,
  `long_running` đúng. Op tốn tiền có `dry_run`.
- Cuối task: chạy **toàn bộ** backend suite (`python -m pytest backend/tests -q -p no:cacheprovider`) → xanh; `ruff check`
  file của mình. Commit `feat(mN): …` + dòng `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Báo cáo cuối (≤ 250 chữ): file đã tạo, op + job đã thêm, lệch hợp đồng (và lý do), đề xuất cột/migration, số test.
