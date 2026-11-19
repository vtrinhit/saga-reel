"""Schema JSON (pydantic) cho đầu ra các công việc LLM `story_*`.

Chặt ở chỗ cần (khoá bắt buộc, kiểu, list không rỗng), lỏng ở khoá thừa (`extra="ignore"`). Giá trị enum của trường
shot KHÔNG kiểm ở đây: `writer` chuẩn hoá (bí danh → chuẩn, lạ → để trống + cảnh báo) để một giá trị lạ không làm hỏng
cả lô clip.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _M(BaseModel):
    model_config = ConfigDict(extra="ignore")


def _strip(v: object) -> object:
    return v.strip() if isinstance(v, str) else v


# --- write_bible ----------------------------------------------------------------------------------------------------


class CharacterOut(_M):
    key: str = Field(min_length=1)
    name: str = Field(min_length=1)
    role: str = ""
    description: str = Field(min_length=1, description="Ngoại hình cụ thể, dùng nguyên văn trong prompt ảnh")
    traits: list[str] = Field(default_factory=list, description="Nhãn nhân thiết (khoá, LLM không được trôi)")
    voice: str = ""
    lock_word: str = ""

    _s = field_validator("key", "name", "description", mode="before")(_strip)


class PlaceOut(_M):
    key: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""


class RelationOut(_M):
    a: str = Field(min_length=1)
    b: str = Field(min_length=1)
    summary: str = Field(min_length=1)


class BibleOut(_M):
    title: str = ""
    logline: str = Field(min_length=1)
    tone: str = ""
    world: str = ""
    style: str = ""
    characters: list[CharacterOut] = Field(min_length=1)
    locations: list[PlaceOut] = Field(default_factory=list)
    props: list[PlaceOut] = Field(default_factory=list)
    relationships: list[RelationOut] = Field(default_factory=list)
    rules: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


# --- outline ----------------------------------------------------------------------------------------------------------


class EpisodeOut(_M):
    number: int = Field(ge=1)
    title: str = Field(min_length=1)
    synopsis: str = Field(min_length=1)
    hook: str = ""
    cliffhanger: str = ""
    characters: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)


class OutlineOut(_M):
    episodes: list[EpisodeOut] = Field(min_length=1)
    arc_summary: str = ""


# --- split_clips ------------------------------------------------------------------------------------------------------


class LineOut(_M):
    speaker: str = "narrator"
    text: str = Field(min_length=1)
    emotion: str = ""


class SceneOut(_M):
    key: str = Field(min_length=1)
    title: str = ""
    location: str = ""
    time_of_day: str = ""
    weather: str = ""


class ClipOut(_M):
    scene: str = ""
    beat: str = Field(default="", description="establishing/master/shot/reverse/insert/reaction/button/action/hook")
    duration_s: float = Field(gt=0, le=60)
    action: str = Field(min_length=1)
    shot_size: str = ""
    angle: str = ""
    camera_move: str = ""
    lens: str = ""
    composition: str = ""
    eyeline: str = ""
    screen_dir: str = ""
    axis_side: str = ""
    cut_in: str = ""
    cut_out: str = ""
    link: str = "cut"
    characters: list[str] = Field(default_factory=list)
    looks: dict[str, str] = Field(default_factory=dict, description="mã nhân vật → mã look")
    location: str = ""
    props: list[str] = Field(default_factory=list)
    dialogue: list[LineOut] = Field(default_factory=list)
    first_frame_desc: str = ""
    last_frame_desc: str = ""
    sfx_desc: str = ""


class SplitOut(_M):
    scenes: list[SceneOut] = Field(default_factory=list)
    clips: list[ClipOut] = Field(min_length=1)


# --- prompts / dialogue -----------------------------------------------------------------------------------------------


class PromptItem(_M):
    clip: str = Field(min_length=1, description="Mã clip (clip.key)")
    prompt: str = Field(min_length=1)


class PromptsOut(_M):
    items: list[PromptItem] = Field(min_length=1)


class DialogueItem(_M):
    clip: str = Field(min_length=1)
    lines: list[LineOut] = Field(default_factory=list)


class DialogueOut(_M):
    items: list[DialogueItem] = Field(min_length=1)


# --- adapt_novel ------------------------------------------------------------------------------------------------------


class FactOut(_M):
    type: str = "fact"
    key: str = ""
    summary: str = Field(min_length=1)
    quote: str = ""


class AdaptOut(_M):
    title: str = Field(min_length=1)
    synopsis: str = Field(min_length=1)
    cliffhanger: str = ""
    cut_after: str = Field(default="", description="Câu nguyên văn trong đoạn truyện nơi tập nên kết (điểm treo)")
    characters: list[CharacterOut] = Field(default_factory=list)
    facts: list[FactOut] = Field(default_factory=list)


# --- memory_summarize ------------------------------------------------------------------------------------------------


class MemoryItem(_M):
    type: str = Field(min_length=1)
    key: str = ""
    summary: str = Field(min_length=1)
    quote: str = ""
    attributes: dict = Field(default_factory=dict)


class MemoryOut(_M):
    summary: str = Field(min_length=1, description="Tóm tắt tập (2–4 câu)")
    arc_summary: str = ""
    items: list[MemoryItem] = Field(default_factory=list)
    open_questions: list[FactOut] = Field(default_factory=list)
    resolved: list[str] = Field(default_factory=list, description="Mã câu hỏi mở đã được giải đáp")
