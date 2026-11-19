"""AI viết + Story Bible (M5, spec §7.4, §14.3–14.4, §15, §23.9).

- `text`: bỏ dấu (đ→d) cho FTS, ước lượng token (4 ký tự/token), cắt theo ngân sách.
- `bible`: CRUD `bible_item` / `bible_evidence`, đồng bộ FTS5 `bible_fts`, tìm không dấu.
- `llmio`: gọi công việc LLM `story_*` (template sửa được), kiểm JSON bằng pydantic, thử lại một lần kèm lỗi.
- `memory`: ngữ cảnh khi viết tập N (bible + tóm tắt nén theo arc + entry FTS liên quan + câu hỏi mở) trong ngân sách.
- `writer`: các bước viết (bible, dàn ý, kịch bản, chia clip, prompt ảnh/motion, thoại, chuyển thể, tóm tắt trí nhớ).
- `author`: chế độ viết `author.<task>` (manual/llm/agent), `context.pack`, `prompts.submit`.
"""
