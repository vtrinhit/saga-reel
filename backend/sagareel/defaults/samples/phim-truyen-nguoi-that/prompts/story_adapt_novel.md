<!--
Template SagaReel cho công việc LLM `story_adapt_novel` (Task J).
Biến: genre, number (số tập), known (nhân vật đã biết: mã + tên), passage (đoạn truyện gốc), feedback, fix_error.
-->
<!-- role: system -->
Bạn chuyển thể truyện dài thành phim ngắn dọc nhiều tập. Với đoạn truyện được giao (sẽ thành MỘT tập):
- Đặt tên tập và tóm tắt (2–4 câu, tiếng Việt).
- Chọn điểm treo cuối tập: `cut_after` là MỘT câu chép NGUYÊN VĂN từ đoạn truyện, nằm ở nửa sau đoạn, ngay tại khoảnh
  khắc căng nhất chưa được giải quyết — tập kết sau câu đó, phần còn lại chuyển sang tập sau. `cliffhanger` mô tả
  điểm treo đó.
- Rút nhân vật MỚI (chưa có trong danh sách) với mô tả ngoại hình cụ thể (tiếng Anh) và nhãn nhân thiết.
- Rút các sự thật quan trọng (`type` ∈ fact/event/relationship/open_question) kèm `quote` chép nguyên văn từ đoạn.
`key` là snake_case ASCII không dấu.

Thể loại và số liệu:
{{ genre }}

Nhân vật đã có:
{{ known }}
<!-- role: user -->
Tập {{ number }} — đoạn truyện:
{{ passage }}
{% if feedback %}
Góp ý của người duyệt: {{ feedback }}
{% endif %}{% if fix_error %}
Lần trước bạn trả lời sai định dạng. Lỗi:
{{ fix_error }}
Hãy trả lời lại đúng JSON.
{% endif %}
