"""Mô hình pydantic của file khai báo project (YAML/JSON) — nguồn của `docs/schema/project-spec.schema.json`.

Nguyên tắc:
- Không có id, dấu thời gian, `rank`, take, cờ trạng thái (`flags`, `review`, `sequence.pipeline/status`): mọi tham chiếu
  là **khoá ổn định** (project key, asset key, look key, sequence key, clip key…); thứ tự = thứ tự trong danh sách.
- Media tham chiếu qua mục trong `media` (sha256 + đường dẫn tương đối so với `data/`).
- Lời thoại và mục timeline **chưa có cột key** (migration 0005 của Task S thêm sau): khoá suy ra theo vị trí —
  lời thoại `<clip>#<n>` theo thứ tự trong clip, mục timeline `<track>@<clip neo>#<n>` theo `offset_ms`. Âm thanh
  (`sound`) cũng chưa có key: nhận diện theo (kind, sha256 media) — `key` trong spec chỉ để mục timeline tham chiếu.
- Trường bỏ trống = giá trị mặc định (khai báo trạng thái đầy đủ của phần được đưa vào). Mục nhóm (`library`,
  `groups`, `sequences`, `presets`) để trống (null) = không đụng tới phần đó.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sagareel.services.presets import KEY_RE as PRESET_KEY_RE
from sagareel.services.projects import KEY_RE as ENTITY_KEY_RE

SPEC_VERSION = 1
SECTIONS = ("presets", "library", "groups", "sequences")
Section = Literal["presets", "library", "groups", "sequences"]

MediaRef = str  # khoá trong `media`


# Khoá đi vào đường dẫn file (project → outputs/specs, outputs/exports; tập → outputs/exports) dùng đúng
# `projects.KEY_RE` như service; khoá preset theo `presets.KEY_RE`. Khoá còn lại (tài sản, look, cảnh, clip, nhóm…)
# có thể do bộ nhập tạo từ mã trong bảng tính (vd. "Lâm") → chỉ chặn ký tự đường dẫn/điều khiển và dấu chấm đầu.
SAFE_KEY = r'^[^./\\\x00-\x1f:*?"<>|][^/\\\x00-\x1f:*?"<>|]{0,127}$'


def _key(desc: str, pattern: str = ENTITY_KEY_RE.pattern) -> Any:
    return Field(min_length=1, max_length=128, pattern=pattern, description=desc)


def _free_key(desc: str) -> Any:
    return _key(desc, SAFE_KEY)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MediaEntry(_Strict):
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$", description="sha256 nội dung file")
    path: str = Field(description="Đường dẫn tương đối so với thư mục data/ (vd. media/2026/11/ab/<sha>.png)")
    kind: str = Field(description="image/video/audio/…")
    mime: str = ""
    size: int = 0
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None


class ProjectBlock(_Strict):
    key: str = _key("Mã project (khoá ổn định)")
    name: str = Field(min_length=1)
    description: str = ""
    genre_key: str = "series"
    style_pack: str | None = Field(default=None, description="Key preset style_pack (của project hoặc toàn cục)")
    aspect: str = "9:16"
    resolution: str = "1080p"
    fps: int = 30
    language: str = "vi"
    languages: list[str] = Field(default_factory=lambda: ["vi"])
    target_platforms: list[str] = Field(default_factory=list)
    style_block: str = ""
    negative_block: str = ""
    settings: dict[str, Any] = Field(default_factory=dict, description="Cài đặt project (trừ *.connection → providers)")
    budget: dict[str, Any] = Field(default_factory=dict)
    cover: MediaRef | None = None


class PresetSpec(_Strict):
    kind: str
    key: str = _key("Key preset (duy nhất theo kind trong project)", PRESET_KEY_RE.pattern)
    name: str
    data: dict[str, Any] = Field(default_factory=dict)


class AssetKindSpec(_Strict):
    key: str = _free_key("Key loại tài sản riêng của project")
    label: str
    icon: str = ""
    ref_priority: int = 0


class VoiceSpec(_Strict):
    key: str = _free_key("Key hồ sơ giọng")
    name: str
    connection: str = Field(default="", description="Tên kết nối TTS")
    voice: str = ""
    rate: str = "+0%"
    pitch: str = "+0Hz"
    volume: str = "+0%"
    style: str = ""
    per_lang: dict[str, Any] = Field(default_factory=dict)
    sample: MediaRef | None = None


class LookSpec(_Strict):
    key: str = _free_key("Key look (duy nhất trong tài sản)")
    name: str
    description_delta: str = ""
    keep_features: list[Any] = Field(default_factory=list)
    lock_word: str = Field(default="", description="Chèn nguyên văn vào prompt")
    anchor: MediaRef | None = None
    sheet: dict[str, MediaRef] = Field(default_factory=dict, description="{góc nhìn: media}")
    refs: list[MediaRef] = Field(default_factory=list)
    valid_from: int | None = Field(default=None, description="Tập bắt đầu (số tập)")
    valid_to: int | None = Field(default=None, description="Tập kết thúc (số tập)")
    is_default: bool = False


class AssetSpec(_Strict):
    key: str = _free_key("Key tài sản (duy nhất trong project)")
    kind: str = Field(description="character/location/prop/… (asset_kind.key)")
    name: str
    description: str = Field(default="", description="Khối khoá, dán nguyên văn")
    locked: bool = False
    voice: str | None = Field(default=None, description="Key hồ sơ giọng")
    tags: list[Any] = Field(default_factory=list)
    custom: dict[str, Any] = Field(default_factory=dict)
    cover: MediaRef | None = None
    looks: list[LookSpec] = Field(default_factory=list)


class SoundSpec(_Strict):
    key: str = _free_key("Nhãn để mục timeline tham chiếu (âm thanh nhận diện theo kind + sha256 media)")
    kind: str = Field(description="music/sfx/ambience")
    title: str
    media: MediaRef
    tags: list[Any] = Field(default_factory=list)
    source: str = ""
    license: str = ""
    attribution: str = ""


class LibrarySpec(_Strict):
    asset_kinds: list[AssetKindSpec] = Field(default_factory=list)
    voices: list[VoiceSpec] = Field(default_factory=list)
    assets: list[AssetSpec] = Field(default_factory=list)
    sounds: list[SoundSpec] = Field(default_factory=list)


class GroupSpec(_Strict):
    key: str = _free_key("Key nhóm tập")
    kind: str = Field(description="series/season/arc")
    title: str
    parent: str | None = Field(default=None, description="Key nhóm cha")
    synopsis: str = ""


class SceneSpec(_Strict):
    key: str = _free_key("Key cảnh (duy nhất trong tập)")
    title: str = ""
    location: str | None = Field(default=None, description="Key tài sản bối cảnh")
    summary: str = ""
    state: dict[str, Any] = Field(default_factory=dict)
    settings: dict[str, Any] = Field(default_factory=dict)


# Khoá phụ (ngoài asset/look/role) được giữ nguyên trong `clip.assets[]`; giá trị chỉ là số/chuỗi/bool. Không bao giờ
# gồm `asset_id`/`variant_id` (id do apply tự giải từ key).
ASSET_REF_EXTRAS = frozenset({"code", "note", "weight", "view"})


class ClipAssetRef(BaseModel):
    model_config = ConfigDict(extra="allow")

    asset: str = Field(description="Key tài sản")
    look: str | None = Field(default=None, description="Key look gắn rõ (bỏ trống = tự chọn theo tập)")
    role: str | None = None

    @model_validator(mode="after")
    def _extras(self) -> ClipAssetRef:
        for k, v in (self.model_extra or {}).items():
            if k not in ASSET_REF_EXTRAS:
                raise ValueError(f"Khoá '{k}' không được phép trong assets[] (cho phép: "
                                 f"{', '.join(sorted(ASSET_REF_EXTRAS))}).")
            if v is not None and not isinstance(v, (str, int, float, bool)):
                raise ValueError(f"Giá trị '{k}' phải là số/chuỗi/bool.")
        return self


class DialogueSpec(_Strict):
    key: str | None = Field(default=None, min_length=1, max_length=128, pattern=SAFE_KEY,
                            description="Khoá ổn định của câu (cột dialogue_line.key); bỏ trống = theo vị trí")
    speaker: str | None = Field(default=None, description="Key tài sản người nói; null = người kể")
    text: str
    emotion: str = ""
    source: str = "tts"
    voice_override: dict[str, Any] = Field(default_factory=dict)
    offset_ms: int = 0
    subtitle: bool = True
    translations: dict[str, Any] = Field(default_factory=dict)


class ClipSpec(_Strict):
    key: str = _free_key("Key clip (duy nhất trong tập)")
    scene: str | None = Field(default=None, description="Key cảnh")
    title: str = ""
    image_prompt: str = Field(default="", description="Nguyên văn")
    image_prompt_src: dict[str, Any] = Field(default_factory=dict)
    motion_prompt: str = Field(default="", description="Nguyên văn")
    motion_prompt_src: dict[str, Any] = Field(default_factory=dict)
    prompt_mode: str = "synced"
    assets: list[ClipAssetRef] = Field(default_factory=list)
    extra_refs: list[MediaRef] = Field(default_factory=list)
    input: MediaRef | None = None
    engine: str = "ai_video"
    motion_preset: str = ""
    duration_ms: int = 6000
    link: str = "cut"
    transition: dict[str, Any] = Field(default_factory=dict)
    trim_in_ms: int = 0
    trim_out_ms: int = 0
    speed: float = 1.0
    grok_audio: str = "ambient"
    subtitle_mode: str = "auto"
    notes: str = ""
    shot_size: str = ""
    angle: str = ""
    camera_move: str = ""
    lens: str = ""
    composition: str = ""
    eyeline: str = ""
    screen_dir: str = ""
    first_frame_desc: str = ""
    last_frame_desc: str = ""
    sfx_desc: str = ""
    beats: list[Any] = Field(default_factory=list)
    custom: dict[str, Any] = Field(default_factory=dict)
    settings: dict[str, Any] = Field(default_factory=dict)
    dialogue: list[DialogueSpec] = Field(default_factory=list, description="Theo thứ tự; khoá = <clip>#<n>")


class TimelineSpec(_Strict):
    key: str | None = Field(default=None, min_length=1, max_length=128, pattern=SAFE_KEY,
                            description="Khoá ổn định của mục (cột timeline_item.key); bỏ trống = theo vị trí")
    track: str = Field(description="music/sfx/title")
    anchor: str | None = Field(default=None, description="Key clip neo (null = đầu tập)")
    offset_ms: int = 0
    end_anchor: dict[str, Any] | None = Field(
        default=None, description='{"clip": key} | {"sequence_end": true} | null')
    duration_ms: int | None = None
    media: MediaRef | None = None
    sound: str | None = Field(default=None, description="Key âm thanh trong library.sounds")
    params: dict[str, Any] = Field(default_factory=dict)


class SequenceSpec(_Strict):
    key: str = _key("Key tập (duy nhất trong project)")
    number: int = 1
    title: str
    group: str | None = Field(default=None, description="Key nhóm tập")
    synopsis: str = ""
    script: str = ""
    workflow_key: str = ""
    gates: dict[str, Any] = Field(default_factory=dict)
    target_platforms: list[str] = Field(default_factory=list)
    settings: dict[str, Any] = Field(default_factory=dict)
    scenes: list[SceneSpec] = Field(default_factory=list)
    clips: list[ClipSpec] = Field(default_factory=list)
    timeline: list[TimelineSpec] = Field(default_factory=list)


class ProjectSpec(_Strict):
    """File khai báo project SagaReel (spec §17)."""

    model_config = ConfigDict(extra="forbid", title="SagaReel project spec")

    spec_version: Literal[1] = Field(description="Phiên bản định dạng (hiện là 1)")
    project: ProjectBlock
    providers: dict[str, str] = Field(
        default_factory=dict, description="Kết nối provider theo loại (image/video/tts/…) — tên kết nối, không có khoá")
    media: dict[str, MediaEntry] = Field(default_factory=dict, description="Media được tham chiếu, theo khoá")
    presets: list[PresetSpec] | None = Field(default=None, description="Preset riêng của project; null = không đụng")
    library: LibrarySpec | None = None
    groups: list[GroupSpec] | None = None
    sequences: list[SequenceSpec] | None = None


def schema_text() -> str:
    """JSON Schema (văn bản ổn định) ghi vào `docs/schema/project-spec.schema.json`."""
    schema = ProjectSpec.model_json_schema()
    schema = {"$schema": "https://json-schema.org/draft/2020-12/schema",
              "$id": "https://sagareel.local/schema/project-spec.schema.json", **schema}
    return json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
