/**
 * Theo dõi job từ sự kiện `job.updated` để dựng thanh tiến độ lô nổi và chip "n đang chạy".
 *
 * HÌNH DỮ LIỆU GIẢ ĐỊNH (Task 7 chưa chốt payload): `{id, status, progress?, stage?, eta_s?|eta_at?, parent_id?,
 * project_id?, type?, label?}`. Mọi chỗ đọc payload nằm trong `normalizeJobEvent` — đổi hình thì chỉ sửa hàm này.
 * - `progress`: chấp nhận 0–1 (phân số) hoặc 0–100; giá trị ≤ 1 coi là phân số.
 * - Lô = `batch_id` ?? `parent_id`; job không có thì gộp vào lô chung.
 */
import { create } from 'zustand';

export const TERMINAL = new Set(['succeeded', 'failed', 'cancelled']);
/** Trạng thái cần người xử lý: tính là lỗi trên thanh lô. */
export const FAILED_LIKE = new Set(['failed', 'interrupted', 'needs_attention']);
const ACTIVE = new Set(['queued', 'running', 'paused', 'waiting_review', 'waiting_input']);

export interface JobSnapshot {
  id: number;
  status: string;
  progress: number | null;
  stage: string | null;
  etaS: number | null;
  batch: string;
  label: string | null;
}

const num = (v: unknown): number | null => {
  if (typeof v === 'number' && Number.isFinite(v)) return v;
  if (typeof v === 'string' && v.trim() !== '' && Number.isFinite(Number(v))) return Number(v);
  return null;
};
const str = (v: unknown): string | null => (typeof v === 'string' && v ? v : null);

export function normalizeJobEvent(data: unknown, now: number = Date.now()): (Partial<JobSnapshot> & { id: number }) | null {
  if (!data || typeof data !== 'object') return null;
  const d = data as Record<string, unknown>;
  const id = num(d.id ?? d.job_id);
  if (id == null) return null;
  const out: Partial<JobSnapshot> & { id: number } = { id };
  const status = str(d.status);
  if (status) out.status = status;
  const p = num(d.progress);
  if (p != null) out.progress = Math.max(0, Math.min(100, p <= 1 ? p * 100 : p));
  const stage = str(d.stage);
  if (stage) out.stage = stage;
  const eta = num(d.eta_s);
  const etaAt = str(d.eta_at);
  if (eta != null) out.etaS = eta;
  else if (etaAt && !Number.isNaN(Date.parse(etaAt))) out.etaS = Math.max(0, (Date.parse(etaAt) - now) / 1000);
  const batch = d.batch_id ?? d.parent_id;
  if (batch != null) out.batch = String(batch);
  const label = str(d.batch_label) ?? str(d.label);
  if (label) out.label = label;
  return out;
}

export interface BatchSummary {
  key: string;
  label: string | null;
  total: number;
  done: number;
  running: number;
  failed: number;
  active: number;
  etaS: number | null;
}

export function summarize(jobs: Record<number, JobSnapshot>): BatchSummary[] {
  const groups = new Map<string, BatchSummary>();
  for (const j of Object.values(jobs)) {
    if (j.status === 'cancelled') continue; // người dùng đã huỷ: không tính vào tổng
    let g = groups.get(j.batch);
    if (!g) {
      g = { key: j.batch, label: j.label, total: 0, done: 0, running: 0, failed: 0, active: 0, etaS: null };
      groups.set(j.batch, g);
    }
    g.total += 1;
    if (j.status === 'succeeded') g.done += 1;
    else if (FAILED_LIKE.has(j.status)) g.failed += 1;
    else if (ACTIVE.has(j.status)) {
      g.active += 1;
      if (j.status === 'running') g.running += 1;
      if (j.etaS != null) g.etaS = Math.max(g.etaS ?? 0, j.etaS);
    }
    if (!g.label && j.label) g.label = j.label;
  }
  return [...groups.values()].filter((g) => g.total > 0);
}

interface JobsState {
  jobs: Record<number, JobSnapshot>;
  applyEvent: (data: unknown) => void;
  /** Bỏ các job đã kết thúc (người dùng đóng thanh lô). */
  dismissFinished: () => void;
  /** `resync` / `server.restarted`: dữ liệu dựng từ sự kiện không còn tin được. */
  reset: () => void;
}

export const useJobs = create<JobsState>((set) => ({
  jobs: {},
  applyEvent: (data) => {
    const patch = normalizeJobEvent(data);
    if (!patch) return;
    set((s) => {
      const prev = s.jobs[patch.id];
      if (!prev && !patch.status) return s; // tiến độ của job chưa biết trạng thái: bỏ qua
      const next: JobSnapshot = {
        id: patch.id,
        status: patch.status ?? prev!.status,
        progress: patch.progress ?? prev?.progress ?? null,
        stage: patch.stage ?? prev?.stage ?? null,
        etaS: patch.etaS ?? (patch.status && TERMINAL.has(patch.status) ? null : prev?.etaS ?? null),
        batch: patch.batch ?? prev?.batch ?? 'all',
        label: patch.label ?? prev?.label ?? null,
      };
      return { jobs: { ...s.jobs, [patch.id]: next } };
    });
  },
  dismissFinished: () =>
    set((s) => ({
      jobs: Object.fromEntries(Object.entries(s.jobs).filter(([, j]) => ACTIVE.has(j.status))) as Record<number, JobSnapshot>,
    })),
  reset: () => set({ jobs: {} }),
}));

export function countActive(jobs: Record<number, JobSnapshot>): { running: number; queued: number; failed: number } {
  let running = 0;
  let queued = 0;
  let failed = 0;
  for (const j of Object.values(jobs)) {
    if (j.status === 'running') running += 1;
    else if (ACTIVE.has(j.status)) queued += 1;
    else if (FAILED_LIKE.has(j.status)) failed += 1;
  }
  return { running, queued, failed };
}
