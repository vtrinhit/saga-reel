"""Hợp đồng prompt (khung của tool, không phải nội dung): mỗi khoá prompt nhận biến gì, trả gì, op nào dùng.

Prompt là dữ liệu của project (bảng `project_prompt`). Tool chỉ công bố hợp đồng để người dùng hoặc AI (qua MCP,
op `prompt.contracts`) tự viết trọn bộ prompt cho một thể loại mới. Tên biến ở đây phải khớp đúng dict biến mà mã
truyền vào khi render (kiểm bằng test parity) — đổi mã thì đổi hợp đồng.

- `kind`: `compile` (Jinja ráp prompt ảnh/video/sheet gửi provider) hoặc `llm` (tin nhắn cho LLM; dòng
  `<!-- role: system|user|assistant -->` tách vai, xem `core/templating.render_messages`).
- `output`: mô tả đầu ra; `output_schema` (JSON Schema, kiểm bằng pydantic) cho bước story_*; `output_format` = khối định
  dạng (defaults/prompt_formats.yaml) tool tự nối vào tin nhắn system khi chạy bằng LLM của tool — prompt của dự án chỉ
  giữ phần dặn dò sáng tạo. Agent gửi thẳng kết quả (story.submit/prompts.submit) theo đúng schema/định dạng này.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

GROUPS = {"compile": "Ráp prompt", "story": "AI viết", "check": "Kiểm tra & khác"}

_LLM_COMMON = {
    "feedback": "Góp ý của người duyệt khi viết lại (có thể rỗng).",
    "fix_error": "Lỗi định dạng của lần trả lời trước (rỗng ở lần đầu) — nhắc LLM sửa.",
}
_INPUT = {"input": "Toàn bộ dữ liệu đầu vào đã soạn sẵn thành chữ (tin nhắn user nên chính là giá trị này)."}


@dataclass(frozen=True)
class Contract:
    key: str
    kind: str  # compile | llm
    group: str  # compile | story | check
    label: str
    purpose: str
    variables: dict[str, str]
    optional: tuple[str, ...] = ()
    output: str = ""  # mô tả; JSON schema thêm qua `schema`
    schema: str | None = None  # tên lớp trong services.story.schemas
    used_by: tuple[str, ...] = ()
    json: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


_SHEET_VARS = {
    "view": "Mã góc nhìn (anchor, front, side…).",
    "view_text": "Mô tả góc nhìn bằng tiếng Anh (từ asset_kinds.yaml).",
    "name": "Tên tài sản.",
    "kind": "Loại tài sản (character/location/prop…).",
    "look_name": "Tên look.",
    "description": "Mô tả khoá của tài sản (nguyên văn).",
    "description_delta": "Mô tả riêng của look (nguyên văn, có thể rỗng).",
    "lock_word": "Cụm từ khoá giữ nhận dạng (có thể rỗng).",
    "keep_features": "Danh sách nét phải giữ (list chuỗi).",
    "style_block": "Khối phong cách của project.",
    "aspect": "Tỉ lệ khung của project (vd. 9:16).",
    "negative": "Khối tránh (project.negative_block hoặc cài đặt prompt.negative_default).",
}


def _sheet(kind: str, label: str) -> Contract:
    return Contract(f"sheet.{kind}", "compile", "compile", label,
                    f"Prompt tạo một góc sheet tham chiếu cho tài sản loại {kind} (góc anchor chỉ chữ, góc khác kèm ảnh "
                    "mỏ neo làm ref).", _SHEET_VARS, used_by=("sheet.generate", "look.preview_prompt"),
                    output="Chữ prompt ảnh gửi provider.")


CONTRACTS: tuple[Contract, ...] = (
    Contract(
        "image.synced", "compile", "compile", "Ráp prompt ảnh khung đầu",
        "Ráp prompt ảnh khung đầu cho clip ở chế độ synced: khối nhân vật (ref + mô tả khoá), phong cách, cảnh, hành "
        "động/máy quay. Giá trị biến chèn nguyên văn.",
        {
            "refs": "Ảnh tham chiếu đã đánh số: list {index, label, role, source} — label dạng 'Image N: <role> of <tên>'.",
            "characters": "Nhân vật trong clip: list {name, description, description_delta, lock_word, keep_features}.",
            "location": "Bối cảnh {name, description, description_delta, lock_word} hoặc null.",
            "props": "Đạo cụ: list {name, description, description_delta, lock_word}.",
            "style_block": "Khối phong cách của project.",
            "aspect": "Tỉ lệ khung (vd. 9:16).",
            "orientation": "Vertical / Horizontal / Square.",
            "scene": "Cảnh {title, summary, conditions, lighting, mood}.",
            "shot": "Ngữ pháp shot {text, eyeline, move} (tiếng Anh).",
            "first_frame": "Mô tả khung đầu của clip (có thể rỗng).",
            "prompt": "clip.image_prompt — phần hành động/cảnh do người hoặc AI viết.",
            "negative": "Khối tránh.",
            "clip": "{key, title} của clip.",
            "continue_frame": "true nếu ref có khung cuối của clip trước (link=continue).",
        },
        optional=("clip", "continue_frame"),
        used_by=("clip.regenerate", "clip.preview_prompt", "pipeline (generate_images)"),
        output="Chữ prompt ảnh gửi provider.",
    ),
    Contract(
        "motion.synced", "compile", "compile", "Ráp prompt video",
        "Ráp prompt image-to-video: chuyển động máy, beat, thoại do model video nói (giọng nhân vật hoặc người kể "
        "ngoài khung), SFX/ambient.",
        {
            "prompt": "clip.motion_prompt — chuyển động/hành động do người hoặc AI viết.",
            "camera": "Chuyển động máy (tiếng Anh, có thể rỗng).",
            "beats": "Beat: list {text, stamp} (stamp 'mm:ss-mm:ss' khi clip ≥ 10 s, ngược lại rỗng).",
            "dialogue": "Thoại do model video nói: list {name, narrator (bool), emotion, text, voice} — voice = "
                        "asset.custom.voice_desc của nhân vật.",
            "language": "Ngôn ngữ thoại (tiếng Anh, vd. Vietnamese).",
            "sfx": "Mô tả hiệu ứng âm thanh (có thể rỗng).",
            "ambient": "Âm nền của cảnh (có thể rỗng).",
            "narrator_voice": "Giọng người kể của project (project.narrator_voice).",
            "clip": "{key, duration_s} của clip.",
        },
        optional=("clip",),
        used_by=("clip.regenerate", "clip.preview_prompt", "pipeline (generate_videos)"),
        output="Chữ prompt video gửi provider.",
    ),
    _sheet("character", "Sheet nhân vật"),
    _sheet("location", "Sheet bối cảnh"),
    _sheet("prop", "Sheet đạo cụ"),
    # --- AI viết truyện ---------------------------------------------------------------------------------------------
    Contract("story_write_bible", "llm", "story", "Viết Story Bible",
             "Từ ý tưởng + số liệu thể loại, viết bible có cấu trúc (nhân vật, bối cảnh, đạo cụ, quan hệ, luật, câu hỏi mở).",
             {"idea": "Ý tưởng của người dùng.", "genre": "Khối số liệu thể loại của project (chữ).", **_LLM_COMMON},
             schema="BibleOut", json=True, used_by=("story.write_bible", "pipeline (write_bible)")),
    Contract("story_outline", "llm", "story", "Lập dàn ý",
             "Lập dàn ý một lô tập (hook đầu tập, điểm treo cuối tập) dựa trên bible + bộ nhớ truyện.",
             {"genre": "Khối số liệu thể loại.", "context": "Story Bible + bộ nhớ truyện (đã cắt theo ngân sách).",
              "arc": "Định hướng arc (có thể rỗng).", "episodes": "Số các tập cần lập, cách nhau dấu phẩy.",
              **_LLM_COMMON},
             schema="OutlineOut", json=True, used_by=("story.outline", "pipeline (outline)")),
    Contract("story_write_script", "llm", "story", "Viết kịch bản",
             "Viết kịch bản một tập (thoại + hành động) — trả CHỮ, UI hiện chữ chạy dần.",
             {"genre": "Khối số liệu thể loại.", "context": "Bộ nhớ truyện.",
              "episode": "Tập cần viết (số + tên + dàn ý + hook + điểm treo).",
              "source": "Đoạn truyện gốc khi chuyển thể (có thể rỗng).",
              "cast": "Nhân vật/bối cảnh trong Library (mã + tên).", **_LLM_COMMON},
             output="Chữ kịch bản (tối thiểu 40 ký tự).", used_by=("story.write_script", "pipeline (write_script)")),
    Contract("story_split_clips", "llm", "story", "Chia kịch bản thành clip",
             "Chia kịch bản thành cảnh + clip (mỗi clip một shot) với trường shot, thoại, mã tài sản.",
             {"genre": "Khối số liệu thể loại.", "rules": "Mẫu phủ cảnh + ngữ pháp shot của tool.",
              "cast": "Mã nhân vật/look/bối cảnh/đạo cụ dùng được.", "enums": "Giá trị hợp lệ của các trường shot.",
              "episode": "Tập (số + tên).", "script": "Kịch bản tập.", **_LLM_COMMON},
             schema="SplitOut", json=True, used_by=("story.split_clips", "pipeline (split_clips)")),
    Contract("story_image_prompts", "llm", "story", "Viết prompt ảnh",
             "Viết clip.image_prompt hàng loạt (chỉ cảnh + hành động; khối nhân vật/phong cách do image.synced ghép).",
             {"clips": "Tóm tắt các clip cần viết (mã, thời lượng, hành động…).", "style": "Khối phong cách project.",
              "cast": "Nhân vật/bối cảnh (mã + tên).", **_LLM_COMMON},
             schema="PromptsOut", json=True, used_by=("story.image_prompts", "pipeline (image_prompts)")),
    Contract("story_motion_prompts", "llm", "story", "Viết prompt chuyển động",
             "Viết clip.motion_prompt hàng loạt (một chuyển động máy, động từ cụ thể; không chép lời thoại).",
             {"clips": "Tóm tắt các clip (mã, thời lượng, hành động, máy, thoại, khung cuối, âm thanh).", **_LLM_COMMON},
             schema="PromptsOut", json=True, used_by=("story.motion_prompts", "pipeline (motion_prompts)")),
    Contract("story_dialogue", "llm", "story", "Viết lời thoại",
             "Viết/rút gọn thoại các clip vừa thời lượng, đúng người nói.",
             {"cast": "Mã người nói dùng được.", "clips": "Clip (mã, thời lượng, hành động, thoại hiện có, giới hạn).",
              **_LLM_COMMON},
             schema="DialogueOut", json=True, used_by=("story.dialogue", "pipeline (dialogue)")),
    Contract("story_adapt_novel", "llm", "story", "Chuyển thể truyện",
             "Đọc một đoạn truyện dài: đặt tên + tóm tắt tập, chọn câu kết làm điểm treo, rút nhân vật và sự thật.",
             {"genre": "Khối số liệu thể loại.", "number": "Số tập.", "known": "Nhân vật đã biết (mã + tên).",
              "passage": "Đoạn truyện gốc.", **_LLM_COMMON},
             schema="AdaptOut", json=True, used_by=("story.adapt_novel",)),
    Contract("story_memory_summarize", "llm", "story", "Ghi bộ nhớ truyện",
             "Tóm tắt tập vừa viết, đề xuất mục Story Bible (sự kiện, trạng thái, sự thật, câu hỏi mở) kèm trích dẫn.",
             {"episode": "Tập (số + tên).", "script": "Kịch bản tập.", "known": "Mã mục Story Bible đã có.",
              "open_questions": "Câu hỏi mở hiện có.", **_LLM_COMMON},
             schema="MemoryOut", json=True, used_by=("story.memory_summarize", "pipeline (memory_summarize)")),
    # --- Kiểm tra & khác ------------------------------------------------------------------------------------------
    Contract("drift_check", "llm", "check", "Kiểm tra lệch nhân vật",
             "So ảnh/khung hình với sheet nhân vật/bối cảnh, phát hiện lệch ngoại hình, trang phục, đạo cụ (kèm ảnh).",
             _INPUT, json=True, output='JSON {"drift": bool, "score": 0..1, "issues": [chuỗi]}.',
             used_by=("drift.check",)),
    Contract("score_take", "llm", "check", "Chấm điểm bản tạo",
             "Xem ảnh/khung hình của một take và chấm điểm theo prompt, nhân vật và lỗi hình ảnh (kèm ảnh).",
             _INPUT, json=True, output='JSON {"score": 0..10, "issues": [chuỗi], "verdict": chuỗi}.',
             used_by=("take.score",)),
    Contract("describe_asset", "llm", "check", "Mô tả tài sản từ ảnh",
             "Xem ảnh tài sản, gợi ý mô tả khoá, lock word và các nét phải giữ (không tự lưu).",
             _INPUT, json=True, output='JSON {"description", "lock_word", "keep_features": [...]}.',
             used_by=("asset.describe_from_image",)),
    Contract("rewrite_prompt", "llm", "check", "Nhờ AI sửa prompt",
             "Viết lại một prompt ảnh/video theo góp ý của người dùng.", _INPUT, output="Chữ prompt mới.",
             used_by=("clip.rewrite_prompt",)),
    Contract("caption", "llm", "check", "Viết caption đăng bài",
             "Viết tiêu đề, caption, hashtag để đăng video.",
             {**_INPUT, "lang": "Mã ngôn ngữ đích (vd. vi)."}, optional=("lang",), json=True,
             output='JSON {"title", "description", "hashtags": [...]}.', used_by=("export.check", "export.create")),
    Contract("translate", "llm", "check", "Dịch",
             "Dịch thoại/phụ đề/caption sang ngôn ngữ khác, giữ giọng văn và độ dài.", _INPUT, json=True,
             output="JSON theo yêu cầu trong input.", used_by=("audio.translate_dub",)),
    Contract("analyze_channel", "llm", "check", "Phân tích kênh mẫu",
             "Đọc ảnh bìa/khung hình + tiêu đề của kênh mẫu, rút ra phong cách, giọng kể, nhịp, công thức nội dung.",
             _INPUT, json=True, output="JSON phân tích (style, pacing, hooks…).", used_by=("channel.analyze",)),
    Contract("clone_scene", "llm", "check", "Viết lại cảnh để clone video",
             "Xem khung hình giữa cảnh của video mẫu, viết prompt ảnh/chuyển động mới theo bảng thay nhân vật.",
             _INPUT, json=True, output='JSON {"image_prompt", "motion_prompt", ...}.', used_by=("clone.from_video",)),
)

BY_KEY: dict[str, Contract] = {c.key: c for c in CONTRACTS}
KEYS: tuple[str, ...] = tuple(BY_KEY)


def get(key: str) -> Contract | None:
    return BY_KEY.get(key)


FORMATS_PATH = Path(__file__).resolve().parent.parent / "defaults" / "prompt_formats.yaml"


@functools.lru_cache(maxsize=1)
def _formats() -> dict[str, str]:
    raw = yaml.safe_load(FORMATS_PATH.read_text(encoding="utf-8")) or {}
    return {str(k): str(v).strip("\n") for k, v in raw.items()}


def output_format(key: str) -> str:
    """Khối định dạng đầu ra (khung của tool) nối vào tin nhắn system khi chạy bằng LLM của tool; "" = trả chữ."""
    return _formats().get(key, "")


@functools.lru_cache(maxsize=None)
def output_schema(key: str) -> dict[str, Any] | None:
    c = BY_KEY.get(key)
    if c is None or c.schema is None:
        return None
    from sagareel.services.story import schemas

    return getattr(schemas, c.schema).model_json_schema()


def to_dict(c: Contract) -> dict[str, Any]:
    return {
        "key": c.key, "kind": c.kind, "group": c.group, "group_label": GROUPS[c.group], "label": c.label,
        "purpose": c.purpose,
        "variables": [{"name": n, "description": d, "required": n not in c.optional} for n, d in c.variables.items()],
        "json": c.json, "output": c.output, "output_schema": output_schema(c.key), "output_format": output_format(c.key),
        "used_by": list(c.used_by),
    }


def check_variables(key: str, used: list[str]) -> list[str]:
    """Cảnh báo (không chặn): biến template dùng mà hợp đồng không có."""
    c = BY_KEY.get(key)
    if c is None:
        return []
    return [f"Biến '{v}' không có trong hợp đồng của '{key}' (render sẽ lỗi thiếu biến)." for v in used
            if v not in c.variables]
