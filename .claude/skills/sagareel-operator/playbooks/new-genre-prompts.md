# Playbook: tạo bộ prompt cho thể loại mới qua MCP

Prompt là dữ liệu của dự án. Tool chỉ công bố hợp đồng (biến, đầu ra) — nội dung do người dùng hoặc AI viết.

1. Khởi đầu: nhân bản dự án mẫu gần nhất (`sr_project_create(name, from_project_id)`) hoặc dự án trống.
2. `sr_prompt_contracts` → với mỗi khoá: mục đích, biến dùng được (`{{ tên }}`), đầu ra. Prompt LLM chỉ viết phần dặn
   dò SÁNG TẠO (vai, giọng văn, luật thể loại); khối định dạng JSON (`output_format`) do tool tự nối khi chạy.
   LLM: tách vai bằng dòng `<!-- role: system -->` / `<!-- role: user -->`. Ráp prompt (`image.synced`,
   `motion.synced`, `sheet.*`): Jinja, biến chèn nguyên văn.
3. Viết từng khoá: `sr_prompt_set(project_id, key, body)` → đọc `warnings` (biến lạ/chưa dùng).
4. Thử với dữ liệu thật: `sr_prompt_preview(project_id, key, body?, clip_id?)` (ráp prompt dùng clip thật; LLM hiện tin
   nhắn đã render với biến thử).
5. `sr_prompt_check(project_id)` → xử lý cảnh báo quan trọng (khoá thiếu, bước sẽ thiếu prompt). Chỉ cảnh báo, không chặn.
6. Đánh dấu mẫu để nhân bản cho dự án sau: `sr_project_update(id, values={is_sample: true})`.
