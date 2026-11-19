/**
 * Kiểu cục bộ cho bảng clip / duyệt / nhập (Task H). Kết quả của `clip.*`, `take.*`, `review.queue`, `sequence.lint`,
 * `sequence.cost_summary`, `import.*` trong `schema.d.ts` là `Record<string, unknown>` (op trả dict tự do) nên viết tay
 * theo `backend/sagareel/services/{clips,takes,sequences,importer}.py`.
 */

export interface TakeMedia {
  id: number;
  kind: string;
  url: string;
  thumb_url: string | null;
  width: number | null;
  height: number | null;
  duration_ms: number | null;
}

/** `services/takes.py::take_out`. */
export interface Take {
  id: number;
  owner_type: 'clip' | 'variant' | 'dialogue';
  owner_id: number;
  part: 'image' | 'video' | 'audio' | 'sheet';
  media_id: number | null;
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled' | string;
  job_id: number | null;
  inputs: Record<string, unknown>;
  parent_take_id: number | null;
  cost: number;
  elapsed_ms: number | null;
  rating: number | null;
  verdict: '' | Verdict;
  notes: string;
  auto_score: Record<string, unknown>;
  created_at: string;
  media: TakeMedia | null;
}

export type Verdict = 'keep' | 'trim' | 'fix_post' | 'regenerate' | 'reject';

export interface ClipAssetRef {
  asset_id: number;
  variant_id?: number | null;
  role?: string;
}

/** Cột của `clip` (`db/models/sequence.py::Clip`) + phần `review.queue` thêm vào. */
export interface Clip {
  id: number;
  sequence_id: number;
  scene_id: number | null;
  rank: string;
  key: string;
  title: string;
  image_prompt: string;
  image_prompt_src: Record<string, unknown>;
  motion_prompt: string;
  motion_prompt_src: Record<string, unknown>;
  prompt_mode: 'verbatim' | 'synced';
  assets: ClipAssetRef[];
  engine: 'ai_video' | 'still_motion' | 'code_anim';
  duration_ms: number;
  link: 'cut' | 'fade' | 'continue';
  image_take_id: number | null;
  video_take_id: number | null;
  flags: Record<string, unknown>;
  review: 'pending' | 'approved' | 'rejected' | 'skipped';
  notes: string;
  shot_size: string;
  angle: string;
  camera_move: string;
  lens: string;
  composition: string;
  eyeline: string;
  screen_dir: string;
  custom: Record<string, unknown>;
  updated_at: string;
  /** chỉ có khi đọc qua `review.queue` */
  take_counts?: Partial<Record<'image' | 'video', number>>;
  selected_takes?: { image: Take | null; video: Take | null };
}

export interface ClipPageFull {
  items: Clip[];
  next_cursor: string | null;
}

export interface Scene {
  id: number;
  sequence_id: number;
  key: string;
  rank: string;
  title: string;
  summary: string;
}

export interface LintWarning {
  code: string;
  level: string;
  clip_id: number;
  clip_key: string;
  message: string;
  related_clip_id?: number;
  related_clip_key?: string;
}

export interface LintResult {
  sequence_id: number;
  clips: number;
  warnings: LintWarning[];
  counts: Record<string, number>;
}

export interface CostSummary {
  sequence_id: number;
  total: number;
  takes: number;
  succeeded: number;
  retries: number;
  by_part: Record<string, { cost: number; takes: number; succeeded: number; retries: number }>;
  per_clip?: { clip_id: number; clip_key: string | null; cost: number; takes: number }[];
}

/** `clip.preview_prompt` ← `compiler.to_dict`. */
export interface PromptPreview {
  text: string;
  refs: { media_id?: number; label?: string; role?: string; url?: string }[];
  warnings: string[];
  provider_caps?: Record<string, unknown>;
}

export interface DialogueLine {
  id: number;
  clip_id: number;
  speaker_asset_id: number | null;
  text: string;
  emotion: string;
  offset_ms: number;
  updated_at: string;
}

export interface EstimatePart {
  jobs: number;
  units: number;
  cost: number;
  connection?: string;
  enabled?: boolean;
  budget?: { enabled: boolean; total: number | null; spent: number; remaining: number | null };
}

/** `clip.regenerate` / `take.regenerate_like`. */
export interface RegenerateResult {
  job_ids: number[];
  take_ids: number[];
  estimate: { jobs: number; takes: number; parts: Record<string, EstimatePart> };
  skipped: { clip_id: number; part: string; code: string; message: string }[];
  warnings: string[];
}

export interface SuggestChange {
  reason: string;
  count: number;
  take_ids: number[];
  message: string;
  suggestions: unknown[];
}

export interface AssetBrief {
  id: number;
  key: string;
  name: string;
  kind: string;
  looks?: { id: number; name: string; key?: string; anchor_media_id?: number | null; is_default?: boolean }[];
}
