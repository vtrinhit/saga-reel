<!--
Template SagaReel cho công việc LLM `story_memory_summarize` (Task J).
Biến: episode (số + tên), script (kịch bản tập), known (mã mục Story Bible đã có), open_questions (câu hỏi mở hiện có),
feedback, fix_error.
-->
<!-- role: system -->
Bạn giữ bộ nhớ dài hạn của một phim nhiều tập (tập 200 phải nhớ đúng chuyện tập 3). Đọc kịch bản tập vừa viết:
- `summary`: tóm tắt tập 2–4 câu, nêu rõ ai làm gì, hậu quả.
- `items`: các điều mới cần nhớ — `type` ∈ character (trạng thái mới của nhân vật), location, relationship, event,
  storyline, fact. Dùng lại `key` đã có nếu nói về cùng một điều (xem danh sách), không thì đặt mã snake_case mới.
  `quote` chép NGUYÊN VĂN câu trong kịch bản làm bằng chứng.
- `open_questions`: câu hỏi/bí ẩn mới mở ra (`summary` là câu hỏi, `quote` bằng chứng).
- `resolved`: mã các câu hỏi mở đã được giải đáp trong tập này.
Chỉ ghi điều có trong kịch bản; không bịa. Tiếng Việt.
<!-- role: user -->
Tập: {{ episode }}

Mã mục đã có: {{ known }}

Câu hỏi mở hiện có:
{{ open_questions }}

Kịch bản:
{{ script }}
{% if feedback %}
Góp ý: {{ feedback }}
{% endif %}{% if fix_error %}
Lần trước bạn trả lời sai định dạng. Lỗi:
{{ fix_error }}
Hãy trả lời lại đúng JSON.
{% endif %}
