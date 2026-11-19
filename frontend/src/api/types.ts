/**
 * Kiểu dữ liệu trả về của các op chưa khai báo `result=` ở backend (OpenAPI để `unknown`). Viết tay theo đúng hàm
 * dựng JSON phía server — đổi ở server thì sửa ở đây:
 * - `Job` ← `services/jobs.py::job_dict` (cũng là payload sự kiện `job.updated`)
 * - `JobLog` ← `services/jobs.py::log_dict` (cũng là payload `job.log`)
 * - `Connection` ← `ops/provider_ops.py::conn_dict`
 * - `Adapter`/`ConfigField` ← `provider.adapters` (`ConfigField.to_dict`)
 * - `Health` ← `providers/base.py::Health.to_dict`; sự kiện `provider.health` = `{id, name, health}`
 */

export type JobStatus =
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'cancelled'
  | 'waiting_review'
  | 'waiting_input'
  | 'paused'
  | 'interrupted'
  | 'needs_attention';

export interface Job {
  id: number;
  type: string;
  status: JobStatus | string;
  priority: number;
  lane: string;
  project_id: number | null;
  sequence_id: number | null;
  clip_id: number | null;
  parent_id: number | null;
  params: Record<string, unknown>;
  /** 0–100 */
  progress: number | null;
  stage: string | null;
  /** ISO có múi giờ (UTC). */
  eta_at: string | null;
  client_ref: string | null;
  external_id: string | null;
  external_state: Record<string, unknown>;
  attempt: number;
  max_attempts: number;
  next_attempt_at: string | null;
  error_code: string | null;
  error_message: string | null;
  hint: string | null;
  result: Record<string, unknown>;
  /** Provider đã giữ việc (`provider_holds_job`): `job.retry` nối lại; muốn tạo lại phải `force_new`. */
  provider_holds: boolean;
  /** Việc đã dừng còn dấu `submitting` chưa có id: chỉ `job.clear_attention` xử lý được. */
  needs_decision: boolean;
  cost_estimate: Record<string, unknown>;
  cost_actual: Record<string, unknown>;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string | null;
}

export interface JobPage {
  items: Job[];
  next_cursor: number | null;
  paused: boolean;
}

export type LogLevel = 'debug' | 'info' | 'warning' | 'warn' | 'error' | string;

export interface JobLog {
  id: number;
  job_id: number;
  level: LogLevel;
  message: string;
  at: string | null;
}

export type ProviderKind = 'image' | 'video' | 'llm' | 'tts' | 'stt';

export type HealthStatus = 'alive' | 'dead' | 'not_configured' | 'unknown';

export interface Health {
  status: HealthStatus;
  detail: string;
  capabilities?: Record<string, unknown>;
  checked_at?: string;
}

export interface Connection {
  id: number;
  kind: ProviderKind;
  adapter: string;
  name: string;
  base_url: string;
  /** Đã che: `••••` + 4 ký tự cuối, hoặc rỗng. */
  api_key: string;
  has_api_key: boolean;
  default_model: string;
  options: Record<string, unknown>;
  concurrency: number;
  enabled: boolean;
  /** `{}` khi chưa kiểm tra lần nào. */
  health: Partial<Health>;
  created_at: string | null;
  updated_at: string | null;
}

export type ConfigFieldType = 'str' | 'secret' | 'int' | 'bool' | 'enum' | 'url' | 'json';

export interface ConfigField {
  name: string;
  label: string;
  type: ConfigFieldType;
  required: boolean;
  default: unknown;
  choices: unknown[] | null;
  help: string;
}

export interface Adapter {
  id: string;
  kind: ProviderKind;
  label: string;
  config_fields: ConfigField[];
  default_capabilities: Record<string, unknown>;
}

export interface ProviderHealthEvent {
  id: number;
  name: string;
  health: Health;
}

/** `estimate` của `provider.try_* dry_run`: `adapter.estimate()` + thông tin kết nối (+ `budget` cho ảnh/video). */
export interface TryEstimate {
  units?: number;
  cost?: number;
  seconds?: number;
  chars?: number;
  kind: ProviderKind;
  connection: string;
  connection_id: number;
  adapter: string;
  enabled: boolean;
  budget?: { enabled: boolean; total: number | null; spent: number; remaining: number | null };
}

export interface DeletePreview {
  impact: {
    connection: { id: number; name: string; kind: string; adapter: string };
    active_jobs: number;
    used_by_settings: string[];
  };
  confirm_token: string;
  expires_in_s: number;
}

export interface MediaItem {
  id: number;
  project_id: number | null;
  kind: string;
  mime: string;
  width: number | null;
  height: number | null;
  duration_ms: number | null;
  size: number;
  created_at: string;
  url: string;
  thumb_url: string | null;
}

/** Mục trong `GET /api/ops`. */
export interface OpCatalogEntry {
  name: string;
  group: string;
  summary: string;
  read_only: boolean;
  destructive: boolean;
  costly: boolean;
  viewer: boolean;
  params_schema?: { properties?: Record<string, unknown>; required?: string[] };
}

/* ------------------------------------------------------------------------------------------------------------------ */
/* W2 — op đã có trên main nhưng `schema.d.ts` (sinh lúc M0) chưa có. Viết tay tối thiểu; người điều phối sinh lại     */
/* schema.d.ts thì chuyển sang `callOp` có kiểu và xoá khối này.                                                      */
/* ------------------------------------------------------------------------------------------------------------------ */

/** `project.get` ← `ops/project_ops.py::ProjectOut` (rút gọn). */
export interface ProjectInfo {
  id: number;
  key: string;
  name: string;
  description: string;
  genre_key: string;
  aspect: string;
  language: string;
  target_platforms: unknown[];
  settings: Record<string, unknown>;
  budget: Record<string, unknown>;
  counts?: Record<string, number>;
}

/** `sequence.get` / mục của `sequence.list` ← `services/sequences.py::seq_out`. */
export interface SequenceInfo {
  id: number;
  project_id: number;
  group_id: number | null;
  key: string;
  number: number;
  title: string;
  synopsis: string;
  script: string;
  status: string;
  workflow_key: string;
  /** Hình dạng do Task J chốt — đọc khoan dung bằng `lib/pipeline.ts`. */
  gates: Record<string, unknown>;
  pipeline: Record<string, unknown>;
  counts: { clips?: number; scenes?: number; duration_ms?: number } & Record<string, number | undefined>;
  updated_at?: string;
}

export interface SequencePage {
  items: SequenceInfo[];
  next_cursor: string | null;
}

/** Mục của `clip.list` (chỉ các trường khung app cần). */
export interface ClipBrief {
  id: number;
  sequence_id: number;
  key: string;
  image_take_id: number | null;
  video_take_id: number | null;
  flags: Record<string, unknown>;
  review: string;
}

export interface ClipPage {
  items: ClipBrief[];
  next_cursor: string | null;
}

/** `history.list` ← `services/history.py::ChangeSetOut`. */
export interface ChangeSet {
  id: number;
  op_name: string;
  /** `user:<id>` · `owner` · `agent:<tên>` · `llm:<…>` … */
  actor: string;
  summary: string;
  created_at: string;
  undone_at: string | null;
  entities: { type: string; id: number; revision_id: number }[];
}

export interface HistoryPage {
  items: ChangeSet[];
  next_cursor: number | null;
}

/** `budget.status` ← `ops/budget_ops.py::BudgetStatus`. */
export interface BudgetKindStatus {
  enabled: boolean;
  total: number | null;
  spent: number;
  remaining: number | null;
}

export interface BudgetStatus {
  image: BudgetKindStatus;
  video: BudgetKindStatus;
}
