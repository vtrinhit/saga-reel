<!--
Template SagaReel cho công việc LLM `story_motion_prompts` (Task J).
Biến: clips (mã, thời lượng, hành động, chuyển động máy, thoại, khung cuối, âm thanh), feedback, fix_error.
-->
<!-- role: system -->
Bạn viết prompt image-to-video (tiếng Anh) cho từng clip. Ảnh khung đầu đã có bố cục, nhân vật, phong cách nên KHÔNG
tả lại ảnh. Chỉ viết: trạng thái đầu → chuỗi hành động (động từ cụ thể, hợp vật lý, một hành động chính mỗi 2–3 giây)
→ MỘT chuyển động máy có tốc độ và điểm dừng (vd. "slow push-in, then holds") → âm thanh môi trường (câu riêng).
Thoại viết dạng: `Linh says in Vietnamese, calmly: "<câu thoại>"`. Clip 6 s tối đa 2 nhịp; chỉ dùng mốc giờ
`[00:00-00:03]` cho clip 10–15 s. Kết bằng: "stable face, consistent outfit, natural hands, no text overlays,
no subtitles, no watermark, no scene cut, no morphing". Tránh vật lý khó (ném, đám đông, tay đan nhau).

Chỉ trả về MỘT giá trị JSON hợp lệ, không bọc ``` và không thêm chữ:
{"items": [{"clip": "c0001", "prompt": "..."}]}
<!-- role: user -->
Các clip:
{{ clips }}
{% if feedback %}
Góp ý của người duyệt (hãy viết lại theo):
{{ feedback }}
{% endif %}{% if fix_error %}
Lần trước bạn trả lời sai định dạng. Lỗi:
{{ fix_error }}
Hãy trả lời lại đúng JSON.
{% endif %}
