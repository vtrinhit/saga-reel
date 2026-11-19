# W2 UI — chỉ dẫn chung cho G, H, I, M

1. Làm trong worktree `D:\MMO\SagaReel_wt\<x>` (nhánh `wt/<x>`, node_modules là junction). Commit trên nhánh; không push;
   không sửa `D:\MMO\SagaReel_Studio`.
2. **Bắt buộc** gọi Skill `frontend-design` và `modern-web-guidance:modern-web-guidance` trước khi viết code; xong gọi
   `hallmark` (audit) trên các màn của mình và sửa phát hiện. Người dùng yêu cầu UI/UX phải thật sự tốt.
3. Đọc theo thứ tự: `docs/design/ui-implementation-brief.md` (§1 kết quả G0, §2 component, **§3 màn của bạn**, §4, §5 bẫy),
   `docs/design/ux-architecture.md`, `DESIGN.md` v2, prototype `docs/design/prototype/<màn>.html` + ảnh
   `docs/design/prototype/shots/`. Khung app (G0) đã có: `components/shell/*`, `components/ui/ActorTag`, `--sr-top`,
   `api/opsLoose.ts` (`callOpLoose`), quy ước phím (`e.preventDefault()` để giữ phím).
4. Kiểu API: `frontend/src/api/schema.d.ts` đã sinh lại đủ op W1+J+K (dùng `callOp` có kiểu). Op chưa có trong schema thì
   `callOpLoose` + kiểu cục bộ đánh dấu. Backend: `backend/sagareel/ops/*_ops.py` là nguồn sự thật cho tham số.
5. Chỉ sửa file của khu mình (`pages/<khu>/**`, `components/<khu>/**`, `lib/<khu>*.ts`) + test. Không sửa `AppShell.tsx`,
   `nav.ts`, `routes.tsx`, `tokens.css`, `tailwind.config.ts`, component `ui/` có sẵn (thêm file mới trong `ui/` được, ghi
   báo cáo). Cần mục điều hướng/token mới → ghi vào báo cáo, không tự sửa.
6. Backend thiếu op/trường để làm đúng thiết kế → **không** sửa backend; liệt kê chính xác (tên op, tham số, kết quả) trong
   báo cáo để người điều phối giao task backend. Màn vẫn phải chạy với op hiện có (ẩn/disable phần thiếu kèm chú thích).
7. Test: vitest (logic + smoke mỗi trang, mock `callOp`), `npm run test -- --run`, `npm run build` (tsc sạch).
8. Chạy thật để xem: backend từ worktree
   `$env:PYTHONPATH="D:\MMO\SagaReel_wt\<x>\backend"; $env:SAGAREEL_DATA_DIR="<thư mục tạm riêng>";
   D:\MMO\SagaReel_Studio\.venv\Scripts\python -m sagareel serve --port <cổng riêng: g 8994, h 8995, i 8996, m 8997>`
   + `VITE_API_PORT=<cổng> npm run dev -- --port <cổng+100>`. Tạo dữ liệu mẫu qua CLI/REST với kết nối `fake_*` (không gọi
   provider thật). Chụp màn 375/768/1280/1920 vào `docs/screenshots/w2/<x>-*.png`; không cuộn ngang, không lỗi console.
   Công cụ trình duyệt có thể tạo `.playwright-mcp/` — xoá trước khi commit.
9. Commit `feat(ui): …` + `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Báo cáo ≤ 300 chữ: màn đã làm, component
   mới, backend còn thiếu (danh sách chính xác), kết quả audit hallmark, ảnh chụp.
