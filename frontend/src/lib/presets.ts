/**
 * Mẫu (preset) — Task M: nhãn loại, trường form cho `platform`/`genre`, đọc/ghi theo đường dẫn, YAML ↔ data.
 * YAML dùng `js-yaml` (đã có trong node_modules qua openapi-typescript; cần khai báo trong package.json — xem báo cáo).
 */
import yaml from 'js-yaml';

export const PRESET_KINDS = [
  { value: 'platform', label: 'Nền tảng' },
  { value: 'genre', label: 'Thể loại' },
  { value: 'workflow', label: 'Quy trình' },
  { value: 'subtitle_style', label: 'Kiểu phụ đề' },
  { value: 'voice', label: 'Giọng' },
  { value: 'transition', label: 'Chuyển cảnh' },
  { value: 'motion', label: 'Chuyển động' },
  { value: 'style_pack', label: 'Style pack' },
  { value: 'channel_brand', label: 'Thương hiệu kênh' },
] as const;

export type PresetKind = (typeof PRESET_KINDS)[number]['value'];

export function kindLabel(k: string): string {
  return PRESET_KINDS.find((x) => x.value === k)?.label ?? k;
}

/** `preset.list` / `preset.get` (`services/presets.py::preset_dict`). */
export interface Preset {
  id: number;
  kind: string;
  key: string;
  name: string;
  data: Record<string, unknown>;
  builtin: boolean;
  /** Cài sẵn đã bị sửa (khác bản gốc). */
  modified: boolean;
  project_id: number | null;
  updated_at?: string;
}

export type FieldType = 'text' | 'int' | 'float' | 'range' | 'enum' | 'list';

export interface FormField {
  path: string[];
  label: string;
  type: FieldType;
  options?: string[];
  hint?: string;
}

const TRANSITIONS = ['cut', 'crossfade', 'fade', 'fadeblack', 'fadewhite', 'dissolve'];

/** Form gọn cho hai loại có schema chặt; còn lại chỉ có YAML. */
export const FORM_FIELDS: Partial<Record<string, FormField[]>> = {
  platform: [
    { path: ['label'], label: 'Nhãn', type: 'text' },
    { path: ['aspect'], label: 'Tỉ lệ', type: 'text', hint: 'vd 9:16' },
    { path: ['width'], label: 'Rộng (px)', type: 'int' },
    { path: ['height'], label: 'Cao (px)', type: 'int' },
    { path: ['fps'], label: 'Khung hình/giây', type: 'int' },
    { path: ['max_size_mb'], label: 'Dung lượng tối đa (MB)', type: 'float' },
    { path: ['duration_s', 'min'], label: 'Thời lượng tối thiểu (s)', type: 'float' },
    { path: ['duration_s', 'max'], label: 'Thời lượng tối đa (s)', type: 'float' },
    { path: ['duration_s', 'recommended'], label: 'Thời lượng nên (s)', type: 'range' },
    { path: ['title', 'max_chars'], label: 'Tiêu đề tối đa (ký tự)', type: 'int' },
    { path: ['caption', 'max_chars'], label: 'Mô tả tối đa (ký tự)', type: 'int' },
    { path: ['hashtags', 'max'], label: 'Số hashtag tối đa', type: 'int' },
    { path: ['loudness', 'target_lufs'], label: 'Độ lớn đích (LUFS)', type: 'float' },
    { path: ['checked_at'], label: 'Ngày tra cứu', type: 'text', hint: 'YYYY-MM-DD' },
  ],
  genre: [
    { path: ['label'], label: 'Nhãn', type: 'text' },
    { path: ['description'], label: 'Mô tả', type: 'text' },
    { path: ['episode_s'], label: 'Độ dài tập (s)', type: 'range' },
    { path: ['clip_s'], label: 'Độ dài clip (s)', type: 'range' },
    { path: ['clips'], label: 'Số clip mỗi tập', type: 'range' },
    { path: ['transition'], label: 'Chuyển cảnh', type: 'enum', options: TRANSITIONS },
    { path: ['end'], label: 'Kết tập', type: 'enum', options: ['cliffhanger', 'loop', 'loop_or_cta', 'cta', 'resolve', 'none'] },
    { path: ['dialogue_style'], label: 'Kiểu lời', type: 'enum', options: ['narration', 'dialogue', 'mixed', 'none'] },
    { path: ['default_platforms'], label: 'Nền tảng mặc định', type: 'list', hint: 'cách nhau bằng dấu phẩy' },
  ],
};

export function getPath(data: unknown, path: string[]): unknown {
  let cur: unknown = data;
  for (const k of path) {
    if (cur == null || typeof cur !== 'object') return undefined;
    cur = (cur as Record<string, unknown>)[k];
  }
  return cur;
}

/** Bản sao có giá trị mới tại `path` (tạo object trung gian khi thiếu). */
export function setPath<T extends Record<string, unknown>>(data: T, path: string[], value: unknown): T {
  const [head, ...rest] = path;
  const copy: Record<string, unknown> = { ...data };
  if (rest.length === 0) {
    if (value === undefined) delete copy[head];
    else copy[head] = value;
  } else {
    const child = copy[head];
    copy[head] = setPath((child && typeof child === 'object' && !Array.isArray(child) ? child : {}) as Record<string, unknown>, rest, value);
  }
  return copy as T;
}

/** Chuỗi trong ô form → giá trị JSON theo kiểu trường; chuỗi rỗng → bỏ trường. */
export function parseField(type: FieldType, raw: string): unknown {
  const t = raw.trim();
  if (t === '') return undefined;
  switch (type) {
    case 'int':
    case 'float': {
      const n = Number(t.replace(',', '.'));
      return Number.isFinite(n) ? (type === 'int' ? Math.round(n) : n) : t;
    }
    case 'range': {
      const parts = t.split(/[–—\-,;]/).map((x) => Number(x.trim().replace(',', '.')));
      return parts.length === 2 && parts.every(Number.isFinite) ? parts : t;
    }
    case 'list':
      return t.split(',').map((x) => x.trim()).filter(Boolean);
    default:
      return t;
  }
}

export function formatField(type: FieldType, v: unknown): string {
  if (v == null) return '';
  if (type === 'range' && Array.isArray(v)) return v.join('–');
  if (type === 'list' && Array.isArray(v)) return v.join(', ');
  return typeof v === 'object' ? JSON.stringify(v) : String(v);
}

export function toYaml(data: unknown): string {
  return yaml.dump(data ?? {}, { lineWidth: 100, noRefs: true, sortKeys: false });
}

export type YamlResult = { ok: true; data: Record<string, unknown> } | { ok: false; error: string; line: number | null };

/** YAML → object; lỗi cú pháp trả câu tiếng Việt + số dòng (1-based). */
export function fromYaml(text: string): YamlResult {
  try {
    const v = yaml.load(text);
    if (v == null) return { ok: true, data: {} };
    if (typeof v !== 'object' || Array.isArray(v)) return { ok: false, error: 'Nội dung phải là một bảng khoá: giá trị (mapping) ở cấp ngoài cùng.', line: 1 };
    return { ok: true, data: v as Record<string, unknown> };
  } catch (e) {
    const err = e as { mark?: { line?: number }; reason?: string; message?: string };
    const line = typeof err.mark?.line === 'number' ? err.mark.line + 1 : null;
    return { ok: false, error: `Lỗi cú pháp YAML${line ? ` ở dòng ${line}` : ''}: ${err.reason ?? err.message ?? 'không đọc được'}.`, line };
  }
}

/** `extra.errors` của `preset_invalid` → dòng "duration_s.recommended: …". */
export function presetErrors(e: unknown): string[] {
  const x = e as { extra?: { errors?: { loc?: unknown[]; msg?: string }[] } } | null;
  return (x?.extra?.errors ?? []).map((r) => `${(r.loc ?? []).join('.') || 'data'}: ${r.msg ?? 'sai'}`);
}

/** Key hợp lệ cho preset mới (giống backend `KEY_RE`). */
export function validPresetKey(k: string): boolean {
  return /^[a-z0-9][a-z0-9_-]{0,63}$/.test(k);
}
