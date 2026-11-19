"""Provider: lớp nền adapter theo năng lực (`base`), registry tự đăng ký (`registry`) và các adapter.

Thêm provider = thêm một file `providers/<kind>/<ten>.py` có lớp kế thừa `ImageAdapter | VideoAdapter | LLMAdapter |
TTSAdapter | STTAdapter` và gắn `@register_adapter` — không sửa lõi. `registry.discover()` tự import mọi module con.
Xem `docs/providers.md`.
"""
