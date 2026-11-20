# M8 — Hiệu năng backend ở quy mô lớn

Dữ liệu thử (`scripts/seed_scale.py`, nhà cung cấp giả): 1 dự án, 200 tập; tập 1 có 1000 clip (mỗi clip 2 câu thoại,
3 take, take ảnh/video đã chọn), 199 tập còn lại 20 clip; 30 mục Story Bible; 50 job nhiều trạng thái; ~190 change set
lịch sử. Tổng ≈ 5000 clip, 10 000 câu thoại, 3000 take. Máy dev Windows 11, SQLite WAL, một process.

Chạy: `pytest backend/tests/test_perf_scale.py -m slow -s` (loại khỏi lần chạy thường bằng `addopts = -m "not slow"`).
Ngân sách trong test ≈ 3× số đo "sau" và có trần số câu SQL (chống N+1) + kiểm EXPLAIN QUERY PLAN không quét toàn bảng
trên `clip`/`dialogue_line`/`take`/`revision`/`change_set`.

| Thao tác | Trước (ms) | Sau (ms) | SQL trước | SQL sau | Ghi chú |
|---|---:|---:|---:|---:|---|
| `clip.list` trang đầu (200) | 31 | 30 | 3 | 3 | đã dùng chỉ mục `ix_clip_sequence_rank` |
| `clip.list` duyệt cursor hết 1000 clip (5 trang) | 230 | 222 | 15 | 15 | — |
| `clip.move` 1 clip trong 1000 | 5,1 | 4,8 | 10 | 10 | đúng 1 UPDATE clip |
| `sequence.list` 200 tập kèm số đếm | 22 | 21 | 6 | 6 | đếm gộp |
| `project.get` | 8,6 | 7,9 | 7 | 7 | — |
| `review.queue` (200) | 56 | 62 | 6 | 6 | — |
| `sequence.lint` (1000 clip) | 152 | 155 | 3 | 3 | thuần Python |
| `sequence.cost_summary` per_clip | 22 | 21 | 5 | 5 | — |
| `pipeline.status` toàn cục | 8,7 | 8,6 | 1 | 1 | quét theo khoá chính + LIMIT |
| `history.list` (50) | 10 | 10 | 2 | 2 | thêm `ix_change_set_project_id_id` |
| `job.list` (50) | 9,1 | 4,9 | 13 | 2 | vị trí hàng đợi: 1 câu thay vì 1 câu/job `queued` |
| `bible.search` | 17 | 3,2 | 62 | 4 | nạp bằng chứng theo lô (cũng áp cho `bible.list`) |
| Dựng layout 1000 clip (`pipeline.load` + `layout.compute`) | 4093 | 426 | 6011 | 19 | nạp trước take/media theo lô + nhớ đường dẫn file theo media |
| `import.preview` 1000 hàng | 39 | 37 | 5 | 5 | — |
| `project.export_spec` (cả dự án 200 tập/5000 clip) | 4000 | ~2100–2700 | 807 | 807 | YAML bằng libyaml (C): nhanh ~4,5× phần ghi, kết quả byte-giống |

Nút thắt còn lại của `export_spec`: ~1,4 s dựng mô hình (ORM nạp 65 000 cột JSON có theo dõi thay đổi) và 4 truy
vấn/tập; tăng tuyến tính theo số tập, chấp nhận được ở quy mô spec.

## Thay đổi

- Migration `0007_perf_indexes`: `job(project_id, id)`, `spend(job_id, kind)`, `change_set(project_id, id)` (chỉ chỉ mục).
- `render/pipeline.load`: nạp take/media theo lô, nhớ đường dẫn file theo media id; `services/media.file_path` nhớ
  `data_dir.resolve()`.
- `services/jobs.queue_positions`: một truy vấn thay cho N+1.
- `services/story/bible`: `item_dicts` (một truy vấn bằng chứng cho cả danh sách).
- `services/spec/export`: dùng `CSafeDumper` khi có libyaml.

## SSE `entity.changed`

`clip.bulk_update` 1000 clip → một sự kiện **12 924 byte** (`entities` là danh sách `[loại, id]`), dưới ngưỡng 64 KB nên
không nén (test khoá ngưỡng 64 KB; nếu vượt thì cần nén id theo khoảng và kiểm `frontend/src/app/useLiveEvents.ts`).
