/**
 * Logic thuần của Thư viện + Dự án (Task G): kiểu dữ liệu theo `services/library.py` / `services/projects.py`,
 * nhãn góc sheet, tìm không dấu, đổi ước tính server → hàng EstimateDialog, kiểm khoảng tập, diff form cài đặt dự án,
 * đọc gợi ý mô tả từ ảnh, gom drift theo tài sản. Không gọi mạng ở đây.
 */
import type { EstimateRow } from '../components/ui/EstimateDialog';
import { foldVi } from './format';

// --- Kiểu dữ liệu (nguồn: backend/sagareel/services/library.py, projects.py) --------------------------------------

export interface ProjectCounts {
  assets: number;
  sequences: number;
  clips: number;
}

/** `project.get` / mục `project.list` ← `ops/project_ops.py::ProjectOut`. */
export interface Project {
  id: number;
  key: string;
  name: string;
  description: string;
  genre_key: string;
  aspect: string;
  resolution: string;
  fps: number;
  language: string;
  languages: string[];
  target_platforms: string[];
  style_block: string;
  negative_block: string;
  settings: Record<string, unknown>;
  budget: Record<string, unknown>;
  cover_media_id: number | null;
  created_at: string;
  updated_at: string;
  counts: ProjectCounts;
}

export interface ProjectPage {
  items: Project[];
  next_cursor: number | null;
}

/** `asset_kind.list` (kèm góc sheet theo `asset_kinds.yaml`). */
export interface AssetKind {
  id: number;
  project_id: number | null;
  key: string;
  label: string;
  icon: string;
  ref_priority: number;
  /** góc → câu mô tả góc (tiếng Anh, dùng trong prompt). */
  views: Record<string, string> | string[];
  default_views: string[];
}

/** Look (asset_variant) ← `library.look_out`. */
export interface Look {
  id: number;
  asset_id: number;
  key: string;
  name: string;
  description_delta: string;
  keep_features: string[];
  lock_word: string;
  anchor_media_id: number | null;
  sheet: Record<string, number>;
  ref_media_ids: number[];
  valid_from: number | null;
  valid_to: number | null;
  is_default: boolean;
  image_count: number;
  warnings: string[];
  updated_at: string;
}

/** Tài sản ← `library.asset_out`. */
export interface Asset {
  id: number;
  project_id: number;
  kind: string;
  key: string;
  name: string;
  description: string;
  locked: boolean;
  voice_profile_id: number | null;
  tags: string[];
  custom: Record<string, unknown>;
  cover_media_id: number | null;
  updated_at: string;
  looks: Look[];
}

export interface AssetPage {
  items: Asset[];
  next_cursor: number | null;
}

/** Take sheet ← `library.take_dict` + `selected`. */
export interface SheetTake {
  id: number;
  view: string;
  media_id: number | null;
  status: string;
  job_id: number | null;
  cost: number | null;
  created_at: string | null;
  selected: boolean;
}

export interface SheetList {
  look: Look;
  views: Record<string, SheetTake[]>;
  selected: Record<string, number | null>;
}

/** `look.preview_prompt` ← `compiler.to_dict`. */
export interface PromptRef {
  media_id: number;
  role: string;
  label: string;
  source: string;
}

export interface PromptPreview {
  text: string;
  refs: PromptRef[];
  warnings: string[];
  provider_caps: Record<string, unknown>;
}

/** `estimate` của `sheet.generate` / `look.generate_matrix` (dry_run). */
export interface ImageEstimate {
  images: number;
  units: number;
  cost?: number | null;
  views?: string[];
  takes?: number;
  connection?: string;
  enabled?: boolean;
  looks?: number;
  budget?: { enabled: boolean; total: number | null; spent: number; remaining: number | null };
}

export interface MatrixItem {
  name: string;
  description_delta: string;
  outfit: string;
  expression: string;
  exists: boolean;
}

export interface Preset {
  id: number;
  kind: string;
  key: string;
  name: string;
  data: Record<string, unknown>;
}

export interface VoiceProfile {
  id: number;
  project_id: number | null;
  key: string;
  name: string;
  voice: string;
  connection: string;
}

export interface BibleItem {
  id: number;
  type: string;
  key: string;
  summary: string;
  attributes: Record<string, unknown>;
  status: string;
  asset_id: number | null;
}

// --- Nhãn ---------------------------------------------------------------------------------------------------------

/** Nhãn tiếng Việt cho góc sheet (khoá lạ giữ nguyên). */
export const VIEW_LABEL: Record<string, string> = {
  anchor: 'Mỏ neo',
  closeup: 'Cận mặt',
  profile: 'Nghiêng',
  back: 'Sau lưng',
  three_quarter: 'Ba phần tư',
  front: 'Chính diện',
  wide: 'Toàn cảnh',
  detail: 'Chi tiết',
  top: 'Từ trên',
  side: 'Bên cạnh',
  establishing: 'Toàn cảnh mở',
  interior: 'Bên trong',
  night: 'Ban đêm',
};

export const viewLabel = (v: string): string => VIEW_LABEL[v] ?? v.replace(/_/g, ' ');

/** Danh sách góc của loại: anchor luôn đầu, rồi góc mặc định, rồi các góc khác. */
export function kindViews(kind: AssetKind | undefined, fallback: string[] = ['anchor']): string[] {
  if (!kind) return fallback;
  const all = Array.isArray(kind.views) ? kind.views : Object.keys(kind.views ?? {});
  const order = [...new Set(['anchor', ...(kind.default_views ?? []), ...all])];
  return order.filter((v) => v === 'anchor' || all.includes(v) || (kind.default_views ?? []).includes(v));
}

/** Góc mặc định (để tạo sheet đủ bộ). */
export function defaultViews(kind: AssetKind | undefined): string[] {
  const d = kind?.default_views?.length ? kind.default_views : ['anchor'];
  return [...new Set(['anchor', ...d])];
}

export const KIND_FALLBACK_LABEL: Record<string, string> = {
  character: 'Nhân vật',
  location: 'Bối cảnh',
  prop: 'Đạo cụ',
};

export const kindLabel = (kinds: AssetKind[] | undefined, key: string): string =>
  kinds?.find((k) => k.key === key)?.label || KIND_FALLBACK_LABEL[key] || key;

/** "Thêm nhân vật" / "Thêm bối cảnh" … */
export function addLabel(kinds: AssetKind[] | undefined, key: string): string {
  const label = kindLabel(kinds, key);
  return `Thêm ${label.charAt(0).toLowerCase()}${label.slice(1)}`;
}

const KIND_ORDER = ['character', 'location', 'prop'];

/** Nhân vật · Bối cảnh · Đạo cụ trước, loại tự thêm sau (theo nhãn). */
export function sortKinds(kinds: AssetKind[]): AssetKind[] {
  const rank = (k: AssetKind) => {
    const i = KIND_ORDER.indexOf(k.key);
    return i < 0 ? KIND_ORDER.length : i;
  };
  return [...kinds].sort((a, b) => rank(a) - rank(b) || a.label.localeCompare(b.label, 'vi'));
}

// --- Tìm, đếm -------------------------------------------------------------------------------------------------------

/** Khớp tên/mã/mô tả/tag/look, gõ không dấu vẫn khớp. */
export function matchAsset(a: Asset, q: string): boolean {
  const needle = foldVi(q.trim());
  if (!needle) return true;
  const hay = foldVi([a.name, a.key, a.description, ...(a.tags ?? []), ...a.looks.map((l) => l.name)].join(' '));
  return needle.split(/\s+/).every((w) => hay.includes(w));
}

export function countByKind(assets: Asset[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const a of assets) out[a.kind] = (out[a.kind] ?? 0) + 1;
  return out;
}

/** Look mặc định (hoặc look đầu). */
export function defaultLook(a: Asset): Look | undefined {
  return a.looks.find((l) => l.is_default) ?? a.looks[0];
}

/** Ảnh đại diện của thẻ: bìa → mỏ neo của look mặc định → mỏ neo bất kỳ. */
export function assetCover(a: Asset): number | null {
  return a.cover_media_id ?? defaultLook(a)?.anchor_media_id ?? a.looks.find((l) => l.anchor_media_id)?.anchor_media_id ?? null;
}

/** Look không phải mặc định "Mặc định" trống → chip trên thẻ. */
export function lookChips(a: Asset): string[] {
  return a.looks.filter((l) => !(l.is_default && l.key === 'default' && l.name === 'Mặc định')).map((l) => l.name);
}

export const thumbUrl = (mediaId: number, w = 320): string => `/media/${mediaId}/thumb?w=${w}`;
export const mediaUrl = (mediaId: number): string => `/media/${mediaId}`;

/** "tập 1–40", "từ tập 5", "tới tập 9", "" */
export function rangeLabel(from: number | null | undefined, to: number | null | undefined): string {
  if (from != null && to != null) return `tập ${from}–${to}`;
  if (from != null) return `từ tập ${from}`;
  if (to != null) return `tới tập ${to}`;
  return '';
}

/** Lỗi khoảng tập (null = hợp lệ). */
export function rangeError(from: number | null, to: number | null): string | null {
  if (from != null && (!Number.isInteger(from) || from < 1)) return 'Tập bắt đầu phải là số nguyên từ 1.';
  if (to != null && (!Number.isInteger(to) || to < 1)) return 'Tập kết thúc phải là số nguyên từ 1.';
  if (from != null && to != null && from > to) return `Tập bắt đầu (${from}) lớn hơn tập kết thúc (${to}).`;
  return null;
}

export function parseEpisode(v: string): number | null {
  const t = v.trim();
  if (!t) return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : NaN;
}

// --- Take sheet ---------------------------------------------------------------------------------------------------

/** Take nên hiện ở ô góc: take đang chọn → take mới nhất xong → take mới nhất. */
export function pickShownTake(takes: SheetTake[] | undefined): SheetTake | undefined {
  if (!takes?.length) return undefined;
  return (
    takes.find((t) => t.selected) ??
    [...takes].reverse().find((t) => t.status === 'succeeded' && t.media_id != null) ??
    takes[takes.length - 1]
  );
}

export const isTakeRunning = (t: SheetTake): boolean => t.status === 'queued' || t.status === 'running';

/** Số góc đã có ảnh chọn trên tổng góc. */
export function sheetProgress(views: string[], selected: Record<string, number | null | undefined>): { done: number; total: number } {
  return { done: views.filter((v) => selected[v] != null).length, total: views.length };
}

/** Ảnh đang chọn cho từng góc từ chính look (khi chưa tải `sheet.list`). */
export function lookSelected(look: Look): Record<string, number | null> {
  return { anchor: look.anchor_media_id, ...(look.sheet ?? {}) };
}

// --- Ước tính -----------------------------------------------------------------------------------------------------

/** Ước tính ảnh của server → hàng EstimateDialog + ngân sách còn + cảnh báo. */
export function estimateToDialog(est: ImageEstimate | undefined): {
  rows: EstimateRow[];
  budgetRemaining: number | null;
  warnings: string[];
  jobCount: number;
} {
  const images = est?.images ?? 0;
  const units = est?.units ?? 0;
  const unit = images > 0 ? units / images : 0;
  const warnings: string[] = [];
  if (est?.enabled === false && est.connection) {
    warnings.push(`Kết nối ${est.connection} đang tắt — bật trong Cài đặt › Kết nối trước khi chạy.`);
  }
  return {
    rows: images > 0 ? [{ kind: 'Ảnh sheet', quantity: images, unitCredits: Math.round(unit * 100) / 100 }] : [],
    budgetRemaining: est?.budget?.remaining ?? null,
    warnings,
    jobCount: images,
  };
}

/** Tách danh sách người dùng gõ ("áo dài đỏ, giáp trận\nhồ ly") thành mục, bỏ trống/trùng. */
export function splitList(text: string): string[] {
  const out: string[] = [];
  for (const raw of text.split(/[,\n;]/)) {
    const t = raw.trim();
    if (t && !out.includes(t)) out.push(t);
  }
  return out;
}

export const MATRIX_MAX = 50;

export function matrixSize(outfits: string[], expressions: string[]): number {
  if (!outfits.length && !expressions.length) return 0;
  return Math.max(outfits.length, 1) * Math.max(expressions.length, 1);
}

// --- Mô tả từ ảnh -------------------------------------------------------------------------------------------------

export interface DescribeSuggestion {
  name: string;
  description: string;
  lock_word: string;
  keep_features: string[];
  tags: string[];
}

/** Đọc `job.result` của `llm.run` task `describe_asset` (`data` = JSON; dự phòng parse `text`). */
export function parseDescribeResult(result: unknown): DescribeSuggestion | null {
  if (!result || typeof result !== 'object') return null;
  const r = result as Record<string, unknown>;
  let data = r.data as Record<string, unknown> | undefined;
  if ((!data || typeof data !== 'object') && typeof r.text === 'string') {
    try {
      const cleaned = r.text.trim().replace(/^```(?:json)?\s*/i, '').replace(/```\s*$/, '');
      data = JSON.parse(cleaned) as Record<string, unknown>;
    } catch {
      return null;
    }
  }
  if (!data || typeof data !== 'object') return null;
  const str = (v: unknown) => (typeof v === 'string' ? v.trim() : '');
  const list = (v: unknown) =>
    Array.isArray(v) ? v.map((x) => (typeof x === 'string' ? x.trim() : '')).filter(Boolean) : [];
  const out = {
    name: str(data.name),
    description: str(data.description),
    lock_word: str(data.lock_word),
    keep_features: list(data.keep_features),
    tags: list(data.tags),
  };
  return out.description || out.lock_word || out.name ? out : null;
}

// --- Drift --------------------------------------------------------------------------------------------------------

export interface DriftClip {
  id: number;
  sequence_id: number;
  assets?: { asset_id?: number; variant_id?: number | null }[];
}

export interface DriftSummary {
  clips: number;
  sequences: number[];
  byLook: Record<number, number>;
}

/** Gom clip bị cờ drift của một tài sản (đếm theo look nếu clip ghi look). */
export function summarizeDrift(clips: DriftClip[], assetId: number): DriftSummary {
  const seqs = new Set<number>();
  const byLook: Record<number, number> = {};
  let n = 0;
  for (const c of clips) {
    const refs = (c.assets ?? []).filter((r) => r.asset_id === assetId);
    if (!refs.length) continue;
    n += 1;
    seqs.add(c.sequence_id);
    for (const r of refs) if (r.variant_id != null) byLook[r.variant_id] = (byLook[r.variant_id] ?? 0) + 1;
  }
  return { clips: n, sequences: [...seqs].sort((a, b) => a - b), byLook };
}

// --- Cài đặt dự án ------------------------------------------------------------------------------------------------

export const ASPECTS = ['9:16', '16:9', '1:1', '4:5'] as const;

export const LANGUAGE_LABEL: Record<string, string> = {
  vi: 'Tiếng Việt',
  en: 'Tiếng Anh',
  zh: 'Tiếng Trung',
  ja: 'Tiếng Nhật',
  ko: 'Tiếng Hàn',
  th: 'Tiếng Thái',
  id: 'Tiếng Indonesia',
  es: 'Tiếng Tây Ban Nha',
};

export const languageLabel = (code: string): string => LANGUAGE_LABEL[code] ?? code;

export interface ProjectForm {
  name: string;
  description: string;
  genre_key: string;
  aspect: string;
  language: string;
  languages: string[];
  target_platforms: string[];
  style_block: string;
  negative_block: string;
  budget_images: string;
  budget_videos: string;
}

const numOrNull = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);

export function projectToForm(p: Project): ProjectForm {
  const b = p.budget ?? {};
  const imgs = numOrNull(b.images_total);
  const vids = numOrNull(b.videos_total);
  return {
    name: p.name,
    description: p.description ?? '',
    genre_key: p.genre_key,
    aspect: p.aspect,
    language: p.language,
    languages: normalizeLanguages(p.language, p.languages ?? []),
    target_platforms: [...(p.target_platforms ?? [])].map(String),
    style_block: p.style_block ?? '',
    negative_block: p.negative_block ?? '',
    budget_images: imgs == null ? '' : String(imgs),
    budget_videos: vids == null ? '' : String(vids),
  };
}

/** Ngôn ngữ chính luôn nằm đầu danh sách ngôn ngữ, không trùng. */
export function normalizeLanguages(primary: string, langs: string[]): string[] {
  return [...new Set([primary, ...langs.map(String)].filter(Boolean))];
}

export interface FormIssues {
  name?: string;
  budget_images?: string;
  budget_videos?: string;
  languages?: string;
}

export function validateProjectForm(f: ProjectForm): FormIssues {
  const issues: FormIssues = {};
  if (!f.name.trim()) issues.name = 'Nhập tên dự án.';
  for (const k of ['budget_images', 'budget_videos'] as const) {
    const v = f[k].trim();
    if (v && !(/^\d+$/.test(v))) issues[k] = 'Nhập số nguyên không âm, hoặc để trống để dùng ngân sách chung.';
  }
  if (!f.languages.length) issues.languages = 'Chọn ít nhất một ngôn ngữ.';
  return issues;
}

/** Chỉ những cột đổi so với dự án gốc (để `project.update` không ghi đè trường người khác vừa sửa). */
export function diffProject(p: Project, f: ProjectForm): Record<string, unknown> {
  const base = projectToForm(p);
  const out: Record<string, unknown> = {};
  const simple = ['name', 'description', 'genre_key', 'aspect', 'language', 'style_block', 'negative_block'] as const;
  for (const k of simple) {
    const v = k === 'name' ? f.name.trim() : f[k];
    if (v !== base[k]) out[k] = v;
  }
  const langs = normalizeLanguages(f.language, f.languages);
  if (JSON.stringify(langs) !== JSON.stringify(base.languages) || 'language' in out) out.languages = langs;
  if (JSON.stringify(f.target_platforms) !== JSON.stringify(base.target_platforms)) out.target_platforms = f.target_platforms;
  if (f.budget_images !== base.budget_images || f.budget_videos !== base.budget_videos) {
    const budget: Record<string, unknown> = { ...(p.budget ?? {}) };
    const set = (key: string, v: string) => {
      if (v.trim() === '') delete budget[key];
      else budget[key] = Number(v.trim());
    };
    set('images_total', f.budget_images);
    set('videos_total', f.budget_videos);
    out.budget = budget;
  }
  return out;
}

/** Nền tảng mặc định của thể loại (`data.default_platforms`). */
export function genreDefaultPlatforms(genre: Preset | undefined): string[] {
  const d = genre?.data?.default_platforms;
  return Array.isArray(d) ? d.map(String) : [];
}

/** Nhãn thể loại cho thẻ dự án. */
export function presetName(list: Preset[] | undefined, key: string): string {
  const p = list?.find((x) => x.key === key);
  return p?.name ?? key;
}
