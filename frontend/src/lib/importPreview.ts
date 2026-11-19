/**
 * Bảng xem trước của Nhập hàng loạt (hàm thuần): đổi trường đã chuẩn hoá của `import.preview` về chuỗi thô để sửa tay,
 * gom chỉnh sửa thành `edits` (chỉ ô đã đổi) cho `import.apply`, đoán định dạng khi dán.
 */

export interface PreviewRow {
  index: number;
  source_row: number;
  fields: Record<string, unknown>;
  errors: string[];
  warnings: string[];
}

export interface PreviewResult {
  columns: string[];
  mapping: Record<string, string | null>;
  fields: string[];
  rows: PreviewRow[];
  assets_to_create: { code: string; kind: string; rows: number[] }[];
  warnings: string[];
  summary: { rows: number; with_errors: number; with_warnings: number };
  preview_token: string;
  expires_at: string;
}

export interface ApplyResult {
  clip_ids: number[];
  scene_ids: number[];
  created_assets: unknown[];
  unresolved_assets: string[];
  dialogue_lines: number;
  skipped: { index: number; reason: string }[];
  change_set_id: number | null;
}

/** Cột sửa được trong bảng (trường chuẩn của importer). */
export const EDIT_COLS: { key: string; label: string; wide?: boolean; mono?: boolean }[] = [
  { key: 'scene', label: 'Cảnh' },
  { key: 'characters', label: 'Nhân vật' },
  { key: 'image_prompt', label: 'Prompt ảnh', wide: true },
  { key: 'motion_prompt', label: 'Prompt chuyển động', wide: true },
  { key: 'dialogue', label: 'Thoại', wide: true },
  { key: 'duration', label: 'Thời lượng', mono: true },
  { key: 'shot_size', label: 'Cỡ cảnh', mono: true },
];

export const FIELD_LABEL: Record<string, string> = {
  scene: 'Cảnh', title: 'Tên clip', characters: 'Nhân vật', image_prompt: 'Prompt ảnh', motion_prompt: 'Prompt chuyển động',
  dialogue: 'Thoại', speaker: 'Người nói', duration: 'Thời lượng', link: 'Nối', engine: 'Engine', connection: 'Kết nối',
  model: 'Model', prompt_mode: 'Chế độ prompt', shot_size: 'Cỡ cảnh', camera_move: 'Chuyển động máy', notes: 'Ghi chú',
  input_media_id: 'Ảnh đầu vào',
};

/** Giá trị chuẩn hoá → chuỗi thô người dùng sửa được (importer đọc lại cùng cú pháp). */
export function rawOf(key: string, f: Record<string, unknown>): string {
  switch (key) {
    case 'characters': {
      const list = (f.characters as { code: string; look?: string }[] | undefined) ?? [];
      return list.map((c) => (c.look ? `${c.code}:${c.look}` : c.code)).join(', ');
    }
    case 'dialogue': {
      const list = (f.dialogue as { speaker?: string; text: string; emotion?: string }[] | undefined) ?? [];
      return list.map((d) => (d.speaker ? `${d.speaker}: ${d.text}` : d.text)).join('\n');
    }
    case 'duration': {
      const ms = f.duration_ms as number | null | undefined;
      return ms ? String(ms / 1000).replace('.', ',') : '';
    }
    default: {
      const v = f[key];
      return v == null ? '' : String(v);
    }
  }
}

/** Chỉ gửi ô đã đổi so với giá trị gốc. */
export function buildEdits(
  rows: PreviewRow[],
  drafts: Map<number, Record<string, string>>,
): { index: number; fields: Record<string, string> }[] {
  const out: { index: number; fields: Record<string, string> }[] = [];
  for (const r of rows) {
    const d = drafts.get(r.index);
    if (!d) continue;
    const fields: Record<string, string> = {};
    for (const [k, v] of Object.entries(d)) if (v !== rawOf(k, r.fields)) fields[k] = v;
    if (Object.keys(fields).length) out.push({ index: r.index, fields });
  }
  return out;
}

export type PasteKind = 'lines' | 'csv' | 'json' | 'yaml';

/** Đoán định dạng văn bản dán: JSON (`[`/`{`), YAML (`- ` hoặc `key:` đầu dòng), CSV (dòng đầu có dấu phẩy/tab, ≥2 cột), còn lại mỗi dòng một clip. */
export function guessPasteKind(text: string): PasteKind {
  const t = text.trim();
  if (!t) return 'lines';
  if (t.startsWith('[') || t.startsWith('{')) return 'json';
  const first = t.split(/\r?\n/)[0];
  if (/^-\s/.test(first) || /^(clips|scenes)\s*:/i.test(first)) return 'yaml';
  const lines = t.split(/\r?\n/).filter(Boolean);
  if (lines.length >= 2 && (first.split('\t').length >= 2 || first.split(',').length >= 2)) {
    const sep = first.includes('\t') ? '\t' : ',';
    const n = first.split(sep).length;
    if (lines.slice(1, 4).every((l) => l.split(sep).length >= Math.min(2, n))) return 'csv';
  }
  return 'lines';
}

/** Loại nguồn theo đuôi file bảng. */
export function fileKind(name: string): 'csv' | 'xlsx' | 'json' | 'yaml' | null {
  const ext = name.toLowerCase().split('.').pop() ?? '';
  if (ext === 'csv' || ext === 'tsv' || ext === 'txt') return 'csv';
  if (ext === 'xlsx' || ext === 'xlsm') return 'xlsx';
  if (ext === 'json') return 'json';
  if (ext === 'yaml' || ext === 'yml') return 'yaml';
  return null;
}

export const isImageFile = (f: { name: string; type?: string }) =>
  (f.type ?? '').startsWith('image/') || /\.(png|jpe?g|webp|gif|bmp|avif)$/i.test(f.name);

/** Sắp file ảnh của thư mục theo tên tự nhiên (01.png < 2.png < 10.png). */
export function sortImageFiles<T extends { name: string; webkitRelativePath?: string }>(files: T[]): T[] {
  const coll = new Intl.Collator('vi', { numeric: true, sensitivity: 'base' });
  return [...files].sort((a, b) => coll.compare(a.webkitRelativePath || a.name, b.webkitRelativePath || b.name));
}
