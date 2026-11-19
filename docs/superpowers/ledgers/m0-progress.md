# SDD ledger — plan: docs/superpowers/plans/2026-11-19-m0-foundation.md

Spec: docs/superpowers/specs/2026-11-19-sagareel-studio-design.md
Branch: m0-foundation (từ main 37502d2). Model subagent: `opus` (alias `sonnet` trên máy này trỏ tới model không tồn tại — lỗi model_not_found).

## Pre-flight scan

| Cặp / task | Sản xuất ↔ tiêu thụ | Phát hiện | Ruling |
|---|---|---|---|
| T1→T2 | `Services.db`, lifespan gọi `migrate()` | khớp | — |
| T3↔T8 | T3 tạm coi mọi request là owner; T8 đặt `request.state.principal` | khớp | — |
| T3↔T6 | `ops.invoke` phải phát `entity.changed` nhưng file list T6 không có `core/ops.py` | thiếu file | Ruling: T6 được sửa `core/ops.py` — yêu cầu nằm trong Interfaces của T6 — rủi ro nhỏ |
| T4↔T5 | T5 ghi revision tay cho `global_settings` (không Tracked); undo của T4 cần map entity_type → model | T4 chưa nói map thế nào | Ruling: T4 dựng map `entity_type`(=tên bảng) → lớp model từ `Base.registry`, áp được cho mọi bảng có revision, kể cả `global_settings` — spec §5.3 muốn mọi thay đổi hoàn tác được — nếu sai, undo settings không chạy |
| T7↔T10 | Worker cần `lane_limit`; T10 cung cấp từ kết nối | T7 test dùng `services.lane_limits` | Ruling: T7 tạo `Services.lane_limits: dict[str,int]` + `lane_limit(lane)` mặc định 2; T10 thay bằng `ProviderService.lane_limit` ưu tiên kết nối rồi rơi về dict — giữ test T7 hợp lệ |
| T7↔T10/T11 | Lỗi adapter phải thành trạng thái job | plan nói "→ job failed code …" nhưng worker chỉ biết `JobFailed/NeedsAttention/TransientError` | Ruling: `ProviderUnavailable`, `ProviderJobError`, `CapabilityError` kế thừa `JobFailed` (code `provider_unavailable`, `provider_<kind>`, `capability_mismatch`); `JobUnknown` kế thừa `NeedsAttention` với code do adapter truyền (MaiVideo: `maivideo_job_unknown`) — worker xử lý đồng nhất, bỏ bước "handler đổi lỗi" ở T11 |
| T10↔T13 | T13 test dùng `fake_llm options.fail` | T10 không định nghĩa | Ruling: T13 được mở rộng `providers/fake/llm.py` thêm `options.fail` |
| T5↔T15 | Tên kết nối mặc định settings (`maivideo`, `groktele`, `edge-tts`) ↔ seed | khớp; LLM dùng `llm-local` qua route task | — |
| T16↔mọi task | parity: op destructive cần `*_preview` | `provider.delete` có `provider.delete_preview`; `token.revoke`, `session.revoke` không đánh dấu destructive | Ruling: revoke không destructive (không mất dữ liệu người dùng) |
| T17↔DESIGN.md | T17 chép `:root` từ DESIGN.md | DESIGN.md đang được agent thiết kế viết | Ruling: T17 chạy cuối; nếu DESIGN.md chưa xong thì chờ |
| T1 | `load_settings()` không tham số tìm root | plan nói "thư mục chứa pyproject" | Ruling: root = `Path(__file__).resolve().parents[2]` |
| T1/T15 | Test tạo app → seed đọc `.env.local` thật | rò key thật vào DB tạm, có thể gọi provider thật | Ruling: fixture `settings` trỏ `env_file` vào `tmp_path/".env.local"` (không tồn tại); khởi động app **không** tự gọi provider |
| T8 | Sau T8, fixture `client` (Host `testserver`) của T3–T7 sẽ 400/401 | test cũ vỡ | Ruling: T8 đổi fixture `client` dùng chung thành cục bộ (`base_url="http://127.0.0.1:8990"`, `client=("127.0.0.1", 50000)`) và sửa test cũ nếu cần |
| T17 step 6 | Kiểm tra trình duyệt + chụp ảnh | subagent có thể không có công cụ trình duyệt | Ruling: implementer T17 làm build + test; người điều phối tự kiểm tra trình duyệt và chụp ảnh |
| T2 | `compare_metadata == []` | cần migration khớp tuyệt đối | khớp với yêu cầu |
| Còn lại (T3, T6, T9, T11, T12, T14) | test ↔ code trong task | tự nhất quán | — |

## Tiến độ
Task 1: dispatched (base 37502d2)
Note: DESIGN.md + docs/design/preview.html xong (agent thiết kế). Font: @fontsource/be-vietnam-pro (400/500/600/700), @fontsource-variable/jetbrains-mono. Accent #F2D24B. Chưa commit — commit sau khi Task 1 xong. Lỗ hổng thiết kế (Presets, Settings LLM routing/budget, Export, Script view, History) → T17 tự thiết kế theo nguyên tắc DESIGN.md.
Task 1: implementer DONE_WITH_CONCERNS (c472f09) — concerns observational (.gitattributes, outputs_dir param, UTF-8 stdout); review dispatched
Task 1: complete (commits 37502d2..c472f09, review clean). Minor để sóng sửa cuối: run.bat pause khi server lỗi; warning TestClient/httpx trong pytest; test ghi vào <repo>/outputs.
(417f6dc: commit DESIGN.md của người điều phối)
Task 2: dispatched (base 417f6dc)
Task 2: implementer DONE (275faac, fix 5c82be7 MutableDict ruling); review dispatched. Note for T4: s.scalar(...).options['x']=1 loses change (weakref) — keep object in variable.
Task 2: complete (commits 417f6dc..5c82be7, review clean). Ruling: tiền tố `fix(m0)` hợp lệ cho commit sửa — ràng buộc nói về commit tính năng — không tốn gì. Minor 1+2 (dispose engine) giao kèm Task 3; minor 3 (docstring weakref) giao kèm Task 4.
Task 3: dispatched (base 5c82be7)
Task 3: implementer DONE (46e1c43, 8e03e90); review dispatched.
Ruling: dry_run/project_id không truyền qua header — op nào cần thì khai báo trong params (plan T10 đã làm vậy) — một nguồn sự thật cho REST/CLI/MCP — chi phí nếu sai: thêm trường vào vài op.
Task 3: complete (commits 5c82be7..8e03e90, review clean). Minor để sóng cuối: fixture test ops try; thứ tự role/validate REST vs invoke; extra.errors msg tiếng Anh (frontend không hiển thị trực tiếp — dùng detail/hint); 405/404 ngoài op chưa theo payload chuẩn.
Task 4: dispatched (base 8e03e90)
Task 4: implementer DONE_WITH_CONCERNS (63a6699, 83b9579). Ruling: UoW dùng BEGIN IMMEDIATE (Database.session(immediate=True)) — chống hoàn tác hai lần khi ghi đồng thời — chi phí nếu sai: ghi tuần tự hoá, chậm hơn chút. Đã gửi implementer sửa.
Ruling (yêu cầu người dùng "chia nhiều agent"): chạy song song theo sóng trong git worktree riêng, người điều phối gộp tuần tự vào m0-foundation rồi review từng task. Sóng A: T6, T9, T16, T17a (frontend khung + UI kit + login + events/api client; trang Settings/Jobs = T17b sau khi backend có). Chi phí nếu sai: xung đột gộp ở app.py/container.py/conftest.py — người điều phối tự giải.
Task 4: fix 2f18961 (BEGIN IMMEDIATE); review dispatched. Wave A dispatched in manual worktrees D:/MMO/SagaReel_wt/{t6,t9,t16,t17a} from 83b9579.
Task 5: dispatched in worktree t5 (base 2f18961)
Ruling (người dùng 2026-11-19): GrokTele không giới hạn → seed (T15) đặt budget.videos_total rất lớn hoặc tắt trần video; giữ trần ảnh 60. Người điều phối chạy thử âm Grok ngay (1 ảnh MaiVideo + 1 clip), không đợi T18 — vì gói GrokTele hết hạn 21/11.
Task 4: review → Needs fixes (1 Important: thay đổi chỉ updated_at tạo change set chặn undo người khác). Important 2 (mask() lộ secret ≤4 ký tự) plan-mandated → Ruling: mask chỉ hiện 4 ký tự cuối khi len ≥ 12, ngược lại "••••" — ràng buộc "không lộ key" thắng câu chữ plan — chi phí nếu sai: khó nhận ra key ngắn trên UI.
Task 4: minor (deferred): redo docstring vs filter; redo ordering NULL; track() timing doc; nested UoW tự khoá 5s; so sánh snapshot sau migration; UnitOfWork dựng tay 3 lần; N truy vấn conflict; -W error fail do Starlette/httpx.
Task 4: minor đưa vào vòng sửa (rẻ, chặn task sau): re-insert mất secret → báo lỗi rõ; OperationalError khoá DB → AppError "db_busy".
Task 5: implementer DONE_WITH_CONCERNS (wt/t5 00f8f3e). Ruling: dữ liệu nội bộ (_app_secret, seeded_env_at, cờ hệ thống) KHÔNG nằm trong global_settings.data mà ở bảng mới `system_state(key unique, value JSON, updated_at)` (Plain, không revision), migration 0002; global_settings.data chỉ chứa khoá có trong schema — tránh lộ secret trong revision và undo xoá secret — T10 (confirm token) và T15 (cờ seed) phải dùng system_state. Ruling: change set của settings.set_global luôn project_id=None (cài đặt toàn cục không thuộc project). Còn lại chấp nhận (snapshot cả dòng; một thực thể cho mọi khoá global).
Task 6: implementer DONE_WITH_CONCERNS (wt/t6 1a2f1ce, 0443998) — concerns observational; review dispatched
Task 6: review → Needs fixes (1 Important: publish từ thread copy data muộn trên loop; lỗi trong callback mất event + nhảy seq).
Task 6: minor (deferred): stale Last-Event-ID sau restart chỉ phát hiện qua boot_id (client phải so boot_id — T17a đã làm); op nhiều project phát project_id=None; append muộn bị bỏ; replay đầy bị resync; publish sau shutdown mất; tiền tố test(m0).
Task 6: minor đưa vào vòng sửa: guard _publish_entity_changed trong finally.
Task 5: implementer DONE after rulings (wt/t5 00f8f3e, 22db342); review dispatched
Task 9: implementer DONE (wt/t9 31e85d7, eca2895); review dispatched. Note T8: auth phải phủ /media.
Task 4: fix round 1 implemented (2f3ccc8); re-review dispatched.
Task 6: fix round 1 implemented (wt/t6 2181ee5); re-review dispatched.
Task 5: complete (commits 2f18961..22db342 on wt/t5, review clean), merged into m0-foundation as e4cd56d; full suite 128 passed.
Task 5: minor (deferred): explain không có tham số scope/extra=forbid; khoá setting secret lưu rõ trong revision (chưa có khoá secret nào); docstring app_secret; float nhận nan/inf; public_hosts nhận JSON bất kỳ; dọn import/docstring.
Task 16: implementer DONE (wt/t16 e13f680, 159a7c9); review dispatched. Ruling: `job wait` hết giờ (timed_out) → mã thoát 4 — agent cần phân biệt "xong" với "chưa xong" — giao trong vòng sửa sau review.
Task 4: fix round 1/5 (4 addressed, 0 open; commits 2f18961..2f3ccc8). Task 4: complete (commits 8e03e90..2f3ccc8, review clean after 1 fix round).
Task 4: minor (deferred): restore no_change khi chỉ đổi updated_at; secret_lost không có đường khôi phục (cần restore-với-key-rỗng + cờ); db_busy chỉ phủ UoW; khớp chuỗi "database is locked".
Task 6: fix round 1/5 (2 addressed, 0 open; wt/t6 0443998..2181ee5). Task 6: complete (review clean), merged into m0-foundation (conflicts app.py/container.py/uow.py giải tay: giữ cả hai; hook _report_committed sau commit); full suite 151 passed.
Task 6: minor (deferred): dict(data) chỉ copy nông.
Task 9: review → Needs fixes (Important: register làm I/O dài trong session → vi phạm luật UoW; gốc media lấy từ thư mục DB thay vì settings.media_dir). Minor đưa vào vòng sửa: _output_parts xoá theo đoán tên; stderr >64KB dừng drain; _place coi mọi OSError là khác ổ.
Task 9: minor (deferred): huỷ hai lần bỏ dọn; dedupe bỏ qua kind/project; log stderr thumb lỗi; test Range 416.
Task 7, Task 8: dispatched in worktrees t7/t8 (base 1532284)
Task 16: review → Needs fixes (Important: job wait timed_out phải exit 4). Ruling (minor 1): mã thoát 5 = lỗi phía app có thể thử lại (5xx, request_timeout, http_error); 2 = request bị từ chối (4xx) — agent phân biệt được thử lại hay sửa input. Minor 3 (handler cuối in exception text) đưa vào vòng sửa.
Task 16: minor (deferred): nullable không đặt null qua flag (ghi help); parity reserved group; giá trị bắt đầu bằng '-'.
Task 16: fix round 1 implemented (wt/t16 18b4729); re-review dispatched.
Task 16: fix round 1/5 (3 addressed, 0 open; wt/t16 159a7c9..18b4729). Task 16: complete (review clean), merged into m0-foundation.
Task 16: minor (deferred): `call job.wait` vẫn exit 0 khi timed_out (chỉ lệnh `job wait` có mã 4); --help khi app ngắt kết nối giữa chừng exit 5.
Task 9: fix round 1 implemented (wt/t9 5959d10); re-review dispatched. Pattern for later tasks: media.prepare → record trong uow → reconcile sau commit.
Task 9: fix round 1/5 (5 addressed, 0 open; wt/t9 eca2895..5959d10). Re-review thấy 2 lỗi mới trong diff sửa: B `_place` xoá bản lưu duy nhất khi src==dest — Ruling: nâng thành Important vì mất dữ liệu; A video_thumb rò .part. → fix round 2.
Task 9: minor (deferred): race reconcile khi register đồng thời cùng bytes (sha không unique); prepare thiếu data_dir mặc định media_dir.parent; reconcile/prepare lỗi chưa bọc AppError; register đổi chữ ký (settings=).
Note for T11 (đo thật 2026-11-19): MaiVideo job 'queued' >10 phút rồi mới completed → timeout poll mặc định nên 1800 s, hết giờ thì TransientError giữ external_id (không POST lại). Lần chạy bị dừng giữa chừng vẫn bị trừ lượt → chống tạo trùng là bắt buộc.
Task 17a: implementer DONE_WITH_CONCERNS (wt/t17a cb4f6fd, 5cd5524); review dispatched (diff không gồm package-lock). Rulings cho T17b: SSE nhận thêm `?last_event_id=` (EventSource mới không gửi được header); payload job.updated chốt theo T7 rồi sửa normalizeJobEvent; bỏ nhánh "404 /api/auth = cục bộ" khi T8 đã gộp; fixture settings trỏ frontend_dist vào tmp.
Task 9: fix round 2/5 (2 addressed, 0 open; wt/t9 5959d10..93044de). Task 9: complete (review clean after 2 rounds), merged into m0-foundation; full suite 237 passed.
Task 9: minor (deferred): orphan tháng cũ bị chuyển sang thư mục tháng hiện tại khi đăng ký lại.
Task 17a: review → Needs fixes (Important: static.py resolve() đường UNC trước kiểm tra chứa → rò SMB/NTLM khi mở từ xa; Esc trong popup đóng cả dialog, dialog lồng đóng cả hai). Important 3 plan-mandated (401 → redirect cứng, mất trạng thái) → Ruling: làm theo DESIGN §5.5 (spec/design thắng plan): modal "Phiên hết hạn" không tắt được, đăng nhập lại tại chỗ rồi gửi lại request; giữ redirect chỉ khi đang ở trang chưa có gì để mất — chi phí nếu sai: thêm một component.
Task 17a: minor đưa vào vòng sửa: detail tiếng Anh từ err.message; đổi project mở SSE không resync + 401 SSE backoff mãi; safeNext dùng URL origin; mimetypes .js/.css/.woff2.
Task 17a: minor (deferred): countdown trong aria-live; 429 không Retry-After; re-render JobsChip; thang progress (chốt ở T7); ConfirmDelete lỗi đóng modal; NetChip role; EstimateDialog key/₫ từng dòng; .vitest/ gitignore; Drawer exit animation, rộng 360.
Task 8: implementer DONE (wt/t8 cb699e1; 12 điểm chặt hơn brief, liệt kê trong report); review dispatched. Note T17b: SPA gửi X-SR-CSRF; Vite dev proxy phải đặt Origin hợp lệ.
Task 7: implementer DONE (wt/t7 71a1b1a, aae6a66). Rulings trước review: (a) recover(): job có external_state.state=="submitting" mà không có external_id → needs_attention (code `job_unknown`, giữ marker) bất kể handler resumable hay load được — spec §5.5 chống tạo trùng thắng brief; job.retry KHÔNG được xoá marker submitting — chỉ job.clear_attention(action=resubmit) mới xoá. (b) Ghi trạng thái cuối thất bại (DB bận) → thử lại vài lần có backoff trước khi bỏ. (c) synchronous=NORMAL giữ nguyên (chấp nhận rủi ro mất điện). (d) T7 được thêm cache_ok=True cho kiểu JSON ở db/base.py (file T2) thay vì né.
Task 10: dispatched in worktree t10 (m0-foundation 936d8b0 + wt/t7 aae6a66 merged = 9c7c69f)
Task 17a: fix round 1 implemented (wt/t17a ed264bc); re-review dispatched.
Task 8: review → Needs fixes. Rulings: I1 (giới hạn đăng nhập bị lợi dụng khoá chủ app): tin đúng MỘT header IP qua setting mới `remote.client_ip_header` (mặc định "cf-connecting-ip"; rỗng = chỉ peer), bỏ qua XFF/X-Real-IP; truy cập is_local miễn bucket peer; bucket toàn cục đổi từ từ chối cứng sang làm chậm dần (tối đa vài giây) để mật khẩu đúng vẫn vào; per-IP vẫn 5/phút cứng. I2 (nginx mặc định gửi Host 127.0.0.1 → ai cũng là chủ) plan-mandated → Ruling: `remote.require_login_local` mặc định TRUE; chỉ có hiệu lực khi đã có mật khẩu owner (chưa có mật khẩu thì cục bộ vẫn là owner để đặt lần đầu) — an toàn mặc định thắng tiện lợi; người dùng đăng nhập 1 lần + nhớ thiết bị 30 ngày; docs/remote-access.md (M8) ghi cấu hình proxy bắt buộc.
Task 8: minor đưa vào vòng sửa: M2 Secure khi Host ∈ public_hosts; M3 chặn Sec-Fetch-Site: cross-site khi ghi; M4 auth.set_password đòi mật khẩu hiện tại khi đã có (trừ cục bộ lần đầu), đổi mật khẩu thu hồi cả token cùng role; M6 SSE kiểm tra lại phiên mỗi 60 s và đóng stream khi bị thu hồi; M8 thêm test (events Bearer, peer cap/xoay header, biến thể đường dẫn, Host chấm cuối/userinfo, websocket).
Task 8: minor (deferred): __Host- cookie prefix; race đặt mật khẩu lần đầu cục bộ; viewer mặc định thấy op read_only (M7 → xét lại cờ viewer ở M1/M7, media gốc cho viewer); tên token không unique; session.list hiển thị ip/user_agent dạng text; scrypt N=2^14 (nâng sau).
Task 7: rulings implemented (wt/t7 7b6addb); full review dispatched (1532284..7b6addb). Note: wt/t10 được tạo từ aae6a66 — khi gộp T7 cuối cùng, T10 cần gộp lại m0.
Task 17a: fix round 1/5 (7 addressed, 0 open; wt/t17a 5cd5524..ed264bc). Task 17a: complete (review clean after 1 round).
Task 17a → T17b (bắt buộc làm): xoá/từ chối hàng đợi pending khi appReady=false (tránh gửi lại POST cũ); session modal luôn là dialog trên cùng (depth ưu tiên); request 401 đến sau re-login → gửi lại ngay (generation); probe auth của stream khi đang reconnect; test SessionExpiredModal; fixture settings frontend_dist → tmp.
Task 17a: minor (deferred): SPA deep link chứa ':' hoặc '/./' → 404; bỏ guard symlink trong dist; SourceBadge Esc khi focus ra ngoài.
Task 17a: merged into m0-foundation; 2 test_static fail vì giả định không có /media thật → giao implementer 17a sửa trong main checkout.
Task 17a: post-merge test fix 01b4b87; full suite 257 passed (verified by coordinator).
Task 7: review → Needs fixes (3 Important: job mang marker submitting tự chạy lại khi TransientError/queued qua restart; ghi trạng thái cuối retry mọi exception → kẹt running; cancel mất khi đang requeue retry). Minor đưa vào vòng sửa: dòng running mồ côi khi mkdir/launch lỗi; retry kiểm status trước marker; clear_attention(fail) không lật job succeeded, xoá error_code/hint cũ, docstring đúng; cancel() có timeout.
Task 7: minor (deferred): supervisor sống sau stop(); except BaseException bắt SystemExit; test coalescing yếu.
Task 8: fix round 1 implemented (wt/t8 00f6a9f); re-review dispatched.
Ruling (việc mới "T8b", làm sau khi gộp T8+T16): (1) `python -m sagareel reset-password` chạy offline trên DB (quyền đọc/ghi thư mục data = quyền chủ máy); (2) file token agent cục bộ `data/local-agent.token` (role owner, tạo lúc khởi động, quyền chỉ user hiện tại) — CLI/MCP tự đọc khi SAGAREEL_TOKEN trống và URL là loopback, để agent trên máy không cần cấu hình sau khi đặt mật khẩu.
Task 7: fix round 1 implemented (wt/t7 eec081a); re-review dispatched. Note T11/T12: adapter phải xoá marker submitting (save_external với state rỗng) TRƯỚC khi ném JobFailed khi provider từ chối rõ ràng (4xx/gateway chưa nhận), nếu không job thành needs_attention/job_unknown.
Task 7: fix round 1/5 (7 addressed; new Important: job succeeded còn marker → không retry/clear được; minor mới: mkdir lỗi 1 job làm đứng cả hàng đợi, _launch lỗi sau đăng ký rò lane count). → fix round 2.
Task 8: fix round 1/5 (6 addressed; open: I1 khi khoá rơi về peer loopback (proxy không header) vẫn khoá chủ app 5/phút; new Important: đăng nhập không giới hạn thông lượng/đồng thời scrypt). Rulings round 2: khoá rơi về peer loopback → làm chậm dần (không 429 cứng); is_local có bucket riêng `local:`; CapacityLimiter(3) cho verify mật khẩu (login + current_password), đếm in-flight trong bucket peer; getlist(header)[-1] + phần tử cuối sau dấu phẩy; khoá limiter validate bằng ipaddress + trần kích thước bảng. Bootstrap hijack trước khi có mật khẩu: cứu bằng reset-password offline (T8b) + banner cảnh báo khi chưa có mật khẩu + docs; current_password cũng qua limiter.
Task 10: implementer DONE_WITH_CONCERNS (wt/t10 5807404, 6e4c16a). Rulings: (1) cột trạng thái vận hành đánh dấu `info={"bookkeeping": True}` (provider_connection.health); UoW/history coi như updated_at: không tính là thay đổi, không so khi kiểm xung đột, không ghi khi undo/redo/restore — sửa core/uow.py + history.py (BOOKKEEPING_COLUMNS lấy từ info cột) — trạng thái runtime không phải dữ liệu người dùng. (2) Ngân sách vượt tối đa concurrency−1 khi chạy song song: chấp nhận, ghi rõ trong docstring. (3) Tên kết nối đã xoá mềm vẫn giữ: chấp nhận, lỗi 409 kèm gợi ý khôi phục hoặc đổi tên. (4) File thêm provider_jobs.py, confirm.py: chấp nhận.
Tasks 11–14: dispatched in worktrees t11..t14 from wt/t10 6e4c16a (song song)
Task 10: rulings implemented (wt/t10 d41371e); review dispatched (9c7c69f..d41371e). Adapters 11–14 based on 6e4c16a (trước d41371e).
Task 7: fix round 2 implemented (wt/t7 340cbac); re-review dispatched.
Task 7: fix round 2/5 (4 addressed, 0 open; wt/t7 eec081a..340cbac). Task 7: complete (review clean after 2 rounds).
Task 7: minor (deferred): external_id '' Python vs SQL không nhất quán; thư mục cache/jobs hỏng → mọi job fail workdir_unavailable trong 1 tick.
Task 7 merged into m0-foundation; full suite 318 passed.
Task 8: fix round 2 implemented (wt/t8 fb2e050); re-review dispatched. Giới hạn còn lại (proxy không header → ~30–60 lần đoán/s song song; nginx cục bộ lấp bucket local) = đặc tính triển khai: docs/remote-access.md phải bắt buộc đặt remote.client_ip_header đúng tunnel.
Task 10: review → Needs fixes (Important: giới hạn vượt ngân sách ghi sai khi nhiều kết nối cùng loại → Ruling: đặt chỗ (reserve) spend trong cùng phiên immediate với kiểm tra, xoá khi tạo lỗi — không còn vượt trần; Important: không có test chặn ngân sách qua ops/handler → adapter trả phí giả trong test). Minor đưa vào vòng sửa: register_adapter từ chối lớp abstract; kiểm tra năng lực resolution/vision; validate option str; che api_key trong error_message của job provider.
Task 10: minor (deferred): adapter module lỗi làm app không khởi động (fail-fast có chủ đích, ghi docs); check kết nối tắt ghi đè health; prompt_file trỏ cache job; assert >=1 lỏng; §18 "chặn ở queued" (để M2).
Task 8: fix round 2/5 (6 addressed, 0 open; wt/t8 00f6a9f..fb2e050). Task 8: complete (review clean after 2 rounds). Ruling residual: proxy ghi Host=127.0.0.1 không header (nginx mặc định) không phân biệt được với cục bộ → yêu cầu triển khai trong docs/remote-access.md (bắt buộc header IP/khuyến nghị cloudflared/Tailscale) + reset-password offline (T8b) là đường cứu.
Task 8: minor (deferred): deque mỗi khoá không maxlen (đặt ~64); hàng verify không giới hạn → đăng nhập chủ app chậm khi bị flood; quét stale O(max_keys); delay() chèn bucket rỗng; nhóm IPv6 /64.
Task 8 merged into m0-foundation; 50 test fail (cli 20, static 18, media 9, jobs 2, security 1) do client Host testserver / auth mới → giao implementer T8 tích hợp trong main checkout (không nới lỏng middleware).
Task 14: implementer DONE_WITH_CONCERNS (wt/t14 588666e, 64a597f). Rulings: (1) ProviderService truyền cache_dir cho adapter (T14 sửa services/providers.py, thay đổi nhỏ); (2) openai_tts/openai_stt option health_speech mặc định TRUE (gửi yêu cầu 1 ký tự) — proxy thiếu credential phải hiện not_configured, không phải alive; (3) job TTS max_attempts=6 (backoff tới 300 s ≈ 10+ phút) vì edge-tts thường tự hồi sau vài phút; (4) file thêm + import aiohttp: chấp nhận.
Task 11: implementer DONE (wt/t11 db6f74d, 476d1db); review dispatched. Accepted: provider.try_image resumable=True.
Ruling (làm sau khi gộp, danh sách "post-merge fixes"): job.retry khi job có external_id (provider đã nhận job) mặc định GIỮ external_id/state để lần chạy sau tiếp tục poll job cũ; chỉ `force_new=true` (rõ ràng, UI hỏi xác nhận "tạo job trả phí mới") mới xoá — tránh trả tiền 2 lần khi poll hết giờ. docs/providers.md bổ sung MaiVideo.
Task 14: rulings implemented (wt/t14 11c2c87) — chưa review (base 6e4c16a).
== TẠM DỪNG (hết hạn mức) ==
Đang chạy lúc dừng: T12 (wt/t12), T13 (wt/t13) implementer; T10 fix round 1 (wt/t10); T8 integration trong main checkout (50 test fail sau gộp); T11 review.
Khi tiếp tục: đọc report các task trên → review T10 fix, T11 kết quả, T12, T13, T14 → gộp t10 rồi t11..t14 vào m0-foundation → post-merge fixes (job.retry giữ external_id; docs providers MaiVideo) → T15 seed → T8b (reset-password offline + local-agent.token) → T17b (Settings/Jobs/Login + danh sách việc 17a) → T18 kiểm chứng thật → review toàn nhánh.
Task 10: fix round 1 implemented (wt/t10 2b4c048, budget reservation); CHƯA re-review — làm đầu tiên khi tiếp tục (fix base d41371e).
Task 12: implementer DONE_WITH_CONCERNS (wt/t12 2704660, 7b00b2d, b5f5a7a, 9eb01b3); CHƯA review.
Post-merge fixes thêm: (a) provider_jobs try_video chỉ DELETE job Grok khi run.cancelled.is_set() (tắt app không được huỷ job đang render); (b) thống nhất khoá marker là `state` (docs/providers.md, test_jobs.py đang dùng `phase`); (c) media.probe chọn stream video đầu tiên không phải attached_pic (mjpeg cover của Grok).
== TIẾP TỤC sau hạn mức ==
Task 11: review → Needs fixes. Important 1 (try_image kiểm tra ngân sách lại khi resume/retry dù job đã trả tiền → fail, mất ảnh) — Ruling: sửa trong post-merge (provider_jobs.py đã đổi sang cơ chế giữ chỗ ở T10 fix; worktree t11 dựa trên bản cũ): bỏ precheck/giữ chỗ khi run.external_id hoặc external.state đã có. Important 2 = ruling job.retry giữ external_id đã ghi (post-merge). Minor đưa vào post-merge: log job_id trước khi save, lỗi save → JobUnknown kèm id; health 429/5xx → unknown thay vì dead; giới hạn ref theo model/health; openai_images không lưu URL ký; docs marker `state`.
Resumed: T8 integration agent, T13 agent. Dispatched: re-review T10 (d41371e..), review T12, review T14.
Task 13: implementer DONE (wt/t13 363a0de, ecaeb42). Ruling: mọi lỗi phía provider (kể cả 4xx model not found/không hỗ trợ) → thử route kế; chỉ lỗi đầu vào của chính app (thiếu biến template…) mới dừng chuỗi — spec §7.3 "chuỗi dự phòng khi model chính lỗi". Override thay route chính cho lần chạy: chấp nhận. Rơi về kết nối LLM bật đầu tiên khi mọi route hỏng: chấp nhận (ghi attempts). Deferred: khoá llm.task.<key> theo tầng (khi có tầng project/sequence ở M1–M2); delete_preview liệt kê task đang route vào kết nối. Note T15: seed openai_compat phải có default_model.
Task 14: complete (wt/t14 6e4c16a..11c2c87, review clean). Chờ gộp sau T10.
Task 14: minor → post-merge: copy cache lỗi OSError → tổng hợp lại; xoá output khi bad_audio; health 429 → unknown, /models 404 vẫn thử speech; 408 → transient; openai_tts read timeout 300 s. Deferred: EdgeTTSException persistent retry 10 phút; cache TTS không evict (gộp vào dọn cache LRU M4).
Task 13: ruling implemented (cd00a7a); review dispatched (6e4c16a..cd00a7a).
Task 8: integration done (ac41046); full suite 461 passed (coordinator re-verified).
Post-merge fixes thêm: viewer không được tải media gốc (?download=1 → 403 cho viewer); CLI help nêu SAGAREEL_TOKEN (sẽ thay bằng local-agent.token ở T8b).
Task 10: fix round 1/5 (6 addressed, 0 open; wt/t10 d41371e..2b4c048). Task 10: complete (review clean after 1 round).
Post-merge fixes thêm (T10): _reserve vào trong try (race huỷ để lại giữ chỗ); job trả phí resumable bỏ qua reserve khi đã có external_id và không release khi tắt app (gộp với ruling T11 Important 1); reserve không xoá dòng đã trả nhưng chưa finalize.
Task 12: review → Needs fixes (Important: gửi lại với clientRef mới giữ external_id cũ → POST timeout bỏ rơi job đã trả). Ruling minor 7: job bị huỷ phía GrokTele khi app tắt → needs_attention (code `remote_cancelled`), KHÔNG tự gửi lại — tôn trọng ý người vận hành. Minor đưa vào vòng sửa: thu hẹp điều kiện gửi lại ref mới (chỉ khi lần chạy mới, external_state rỗng lúc vào); che accountLabel/message GrokTele; samefile; resume không cần ảnh đầu vào khi đã có external_id.
Task 10 merged into m0-foundation; 1 test fix khi gộp (client cục bộ) bởi người điều phối.
Task 11: complete (review: adapter OK; Important 1+2 chuyển post-merge theo ruling). Merged into m0-foundation (conflict provider_jobs: giữ @provider_job của T10, CHƯA resumable → test_try_image_is_resumable đỏ có chủ đích, post-merge làm resumable + bỏ reserve khi đã có external_id + không release khi tắt app). Suite: 1 failed (đó), 578 passed.
Task 12: fix round 1 implemented (wt/t12 d874db3); re-review dispatched.
Task 13: review → Needs fixes (Important: phản hồi rỗng/không SSE coi là thành công → bỏ qua dự phòng; usage thiếu khi stream — cần stream_options include_usage có tuỳ chọn tắt). Minor đưa vào vòng sửa: lỗi render template khác → 422 template_render_failed; pin jinja2>=3.1.6; finish_reason=length → cảnh báo/attempt; fallback thiếu connection → lỗi; fake_llm fail kiểu bool.
Task 13: minor (deferred): retry job chạy lại mọi route; giới hạn song song chỉ áp kết nối chính; input rỗng bỏ user message; test stream tách dòng thật.
Task 12: fix round 1/5 (6 addressed, 0 open; wt/t12 9eb01b3..d874db3). Task 12: complete.
Task 12: minor (deferred): POST đầu bị từ chối rõ → lần sau không còn "fresh"; ảnh đầu vào bị xoá sau POST mơ hồ → invalid_input; username không '@' không bị che.
Task 12 merged; suite 1 failed (test_try_image_is_resumable, có chủ đích), 625 passed. Post-merge A (main checkout) + B (wt/pm-b) dispatched.
T8b, T17b dispatched in worktrees from m0-foundation da9c296.
Task 13: fix round 1 implemented (wt/t13 c90e691); re-review dispatched.
Task 13: fix round 1/5 (7 addressed, 0 open; wt/t13 cd00a7a..c90e691). Task 13: complete. Chờ gộp sau post-merge A (đang dùng main checkout).
Task 13: minor (deferred): regex unknown-field rộng → 1 request thừa; phản hồi chỉ khoảng trắng coi là thành công; thiếu reset delta khi mọi route lỗi; test template.update chặn include.
Task 15: dispatched in worktree t15 (da9c296 + wt/t13).
LỖI người điều phối: vòng gộp 'for t in 11 14' dừng ở conflict t11 → wt/t14 CHƯA vào m0. Ruling: pm-b gộp wt/t14 + wt/t13 trong worktree của nó; khi gộp pm-b vào m0 là có cả T13, T14. T15 (worktree) đã có T13 nhưng chưa có T14 — gộp sau.
Post-merge A: DONE_WITH_CONCERNS (835c2db); suite 645 passed. Rulings: (1) retry_job (force_new hoặc job thành công không external_id) TÁCH dòng spend cũ (job_id=NULL, giữ lại) thay vì để reserve xoá — chi tiêu đã trả không bao giờ mất; reserve chỉ xoá giữ chỗ chưa trả của lần chạy hiện tại. (2) retry có params_patch trên job có external_id mà không force_new → 422 `params_patch_requires_new_job` (gợi ý force_new). (3) sửa hint adapter "chạy lại" cho đúng (resume vs force_new vs clear_attention). (4) giữ chỗ khi needs_attention/clear_attention(fail) vẫn tính — chấp nhận (thận trọng), ghi docs. (5) max_attempts qua provider_job: không cần.
Task 15: implementer DONE_WITH_CONCERNS (wt/t15 f4b654c, 76e981f) — concerns chấp nhận; review dispatched.
T8b: implementer DONE_WITH_CONCERNS (wt/t8b d713971, 3abfa76). Accepted: phát hiện app đang chạy bằng locking_mode EXCLUSIVE; CLI gửi token file sau 401; đổi mật khẩu owner trong app viết token mới. Rulings: (a) token local-agent chỉ được chấp nhận khi request là cục bộ thật (loopback peer, Host cục bộ, không header chuyển tiếp) — token lộ cũng không dùng được qua tunnel; (b) reset owner = cứu hộ sau xâm nhập: thu hồi MỌI session + token mọi role (rồi tạo lại local-agent token), xoá mật khẩu viewer trừ khi `--keep-viewer`.
Post-merge A: rulings implemented (f28323e); suite 657 passed; concern clear_attention(resubmit) thay giữ chỗ khi chỉ có marker: chấp nhận (người dùng xác nhận provider chưa tạo). Review dispatched.
Task 15: complete (wt/t15 62422db..76e981f, review clean). Chờ gộp.
Final-wave list (M0): engine hide_parameters=True (secret có thể vào log lỗi SQL — op provider.create/import_env); seed: kiểm marker trước khi đọc env; videos_total=0 chỉ khi tạo groktele; test BOM/CRLF + test khởi động lỗi seed không lộ secret; deque limiter maxlen; các minor deferred khác do final reviewer phân loại.
Post-merge B: done (wt/pm-b 27a35e6); review dispatched (own commits only).
Post-merge B: review → Needs fixes (Important: job_id MaiVideo chỉ ghi qua run.log vào DB đang hỏng → mất dấu job đã trả; cần ghi thêm app log). Minor vào vòng sửa: docs multikit health; gộp helper phân loại health dùng chung; role check fail-closed. Ruling minor 6: viewer xem inline media gốc tạm chấp nhận (chưa có proxy); M4 khi có proxy/preview → viewer chỉ được proxy/thumb.
Post-merge A: review → Needs fixes. Critical: openai_images (state="completed", không external_id) — retry thường/clear_attention(resubmit) xoá dòng chi tiêu đã trả + POST lại. Important: force_new/resubmit không đổi clientRef MultiKit → GrokTele gộp về job cũ. Rulings: "provider giữ job" = external_id HOẶC external_state.state ∉ {None, "submitting"}; khi đó retry thường = nối lại (giữ external_state), params_patch → 422, force_new → detach; clear_attention detach trừ khi (không external_id AND state=="submitting" AND resubmit). Mỗi lần force_new/clear_attention(resubmit) tăng `gen` trong external_state; MultiKit clientRef = sagareel:job:<id> khi gen=0, :g<gen> khi gen>0. Retry thường job succeeded có provider giữ → 409 `already_succeeded` gợi ý force_new. Minor 3: _paid nhận biết tắt app bằng cờ stopping của worker (không chỉ suy từ run.cancelled).
T8b: rulings implemented (wt/t8b f9fdfa0, migration 0003 local_only); review dispatched.
Post-merge B: fix round 1 implemented (9d5b419); re-review dispatched.
Post-merge B: fix round 1/5 (4 addressed, 0 open; 27a35e6..9d5b419). Post-merge B: complete. Chờ gộp (pm-b mang theo T13+T14) sau khi A xong trong main checkout.
Post-merge A: fix round 1 implemented (a3a464c); ruling thêm: clear_attention(resubmit) chỉ tăng gen khi provider giữ job; marker-only giữ gen (GrokTele dedupe).
T8b: review → Needs fixes (Important: reset-password trên DB chưa có migration 0003 → lỗi chung + đã ghi đè file token). Minor vào vòng sửa: lock quanh ensure_local_agent_token; áp lại ACL khi giữ file; icacls theo SID (whoami /user); mô tả parser; test URL lừa; dòng thành công dùng đường dẫn thật. Deferred → docs/remote-access.md (M8): cổng 8990 bị chiếm khi app tắt có thể lấy token (mô hình tin cậy cục bộ); tunnel TCP thô trông như cục bộ.
Task 17b: implementer DONE (wt/t17b 8a5f2ed, 7389b06; thêm op budget.status — chấp nhận); review dispatched.
Post-merge A: fix round 1 + follow-ups implemented (a3a464c, 2dcc6a4, a5ab80d); re-review dispatched (f28323e..a5ab80d).
T8b: fix round 1 implemented (a9faaef); re-review dispatched.
Post-merge B (+T13, T14) merged into m0-foundation; conflicts maivideo import/test/docs giải tay; full suite 852 passed, 0 failed.
T8b: fix round 1/5 (6 addressed, 0 open). T8b: complete. Minor deferred: fallback tên người dùng khi không lấy được SID (nên bỏ qua ACL thay vì grant theo tên); regex SID lấy trường cuối CSV; DB mới hơn code báo sai thông điệp.
T8b + T15 merged into m0-foundation (conflict app.py: seed trước rồi local-agent token); full suite 943 passed, 1 skipped. Còn: re-review post-merge A, review T17b → gộp T17b → T18 kiểm chứng thật → final review.
Post-merge A: fix round 1/5 (4 addressed, 0 open; f28323e..a5ab80d). Post-merge A: complete. Không còn đường gửi trả phí lần 2 tự động; không job kẹt.
Final-wave list thêm (tiền, hẹp, cần thao tác người): (A) job có external_id + state=="submitting" (ref một-lần MultiKit) → clear_attention(resubmit) giữ ref đã lưu, không tăng gen; (B) resubmit marker-only của MultiKit: không release giữ chỗ (detach/giữ) vì GrokTele có thể đã tạo; clear_attention(fail) trên job provider giữ → tăng gen; docs nit xuống dòng.
Task 17b: review → Needs fixes (Important: token API thô + api_key gõ vào nằm trong cache mutation TanStack/state). Minor vào vòng sửa: lỗi trang sau không xoá danh sách; so updated_at khi gộp sự kiện vs fetch; phân trang log job; vai trò viewer ẩn nút ghi; thống nhất "việc"/"Prompt cuối"; mapping error_code tiếng Việt thêm các mã phổ biến; EstimateDialog "ngân sách tháng" sai.
Task 17b: minor (deferred): JobRow role=button chứa nút con (a11y) → M8 polish.
T18: kiểm chứng thật xong (LLM, TTS, ảnh MaiVideo 44 s, video Grok 73 s) — docs/research/m0-provider-check.md. Ảnh 3/60.
Task 17b: fix round 1 implemented (fef99a7); re-review dispatched. Final-wave thêm: worker cập nhật updated_at khi ghi tiến độ (frontend không so được độ mới).
Task 17b: fix round 1/5 (6 addressed, 0 open). Task 17b: complete. Final-wave thêm: pickJob hoà updated_at → xếp hạng trạng thái (terminal > running > queued); actionError giữ detail/hint; useIsViewer khi đang tải; hint backend còn chữ "job" → "việc"; TryDialog dùng ERROR_TEXT.
UI check: 7 trang × 4 kích thước không cuộn ngang, không lỗi console; ảnh ở docs/screenshots/m0. Final-wave thêm: việc đã xong vẫn hiện stage cũ.
Final review: With fixes (1 must-fix hide_parameters; Important: retry UI vs provider_holds, schema.d.ts stale). Final fix wave dispatched (base b28ca33).
Final fix wave: implemented (b28ca33..cd38546); backend 963 passed, vitest 126, build ok. Re-review dispatched.
Final fix wave: re-review clean (all 8 addressed). M0 complete.
