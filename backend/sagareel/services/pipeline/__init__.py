"""Quy trình (workflow) theo preset dữ liệu (spec §13, phụ lục film-craft §2, §5).

- `workflow`: kiểm/tải preset `workflow` (bước + bật/tắt + gate + cách chạy lô).
- `engine`: job dài `pipeline.run` (mỗi tập một job), đi từng bước, sinh job con và chờ, dừng ở gate
  (`waiting_review`) / bước viết tay hoặc agent (`waiting_input`), tiếp tục sau `gate.approve` / `prompts.submit`,
  chạy lại bước sau `gate.reject(feedback)`.
"""
