/**
 * Luồng "Thử" một kết nối (DESIGN nguyên tắc 6: cái gì tốn tiền thì báo giá trước):
 * form → (ảnh/video/TTS) `dry_run` → EstimateDialog → chạy → theo dõi job; (LLM/STT — backend chưa có `dry_run`)
 * form → bước xác nhận "chưa ước tính được" → chạy → theo dõi job. Thuần, không có React.
 */
import type { ApiError } from '../api/client';
import type { ProviderKind, TryEstimate } from '../api/types';
import type { EstimateRow } from '../components/ui/EstimateDialog';

export type TryStep = 'form' | 'estimating' | 'estimate' | 'confirm' | 'running' | 'job';

export interface TryState {
  step: TryStep;
  estimate: TryEstimate | null;
  jobId: number | null;
  error: ApiError | null;
}

export type TryAction =
  | { type: 'submit'; dryRun: boolean }
  | { type: 'estimated'; estimate: TryEstimate }
  | { type: 'run' }
  | { type: 'started'; jobId: number }
  | { type: 'failed'; error: ApiError }
  | { type: 'back' }
  | { type: 'reset' };

export const INITIAL: TryState = { step: 'form', estimate: null, jobId: null, error: null };

const DRY_RUN_KINDS: ReadonlySet<ProviderKind> = new Set(['image', 'video', 'tts']);

export function supportsDryRun(kind: ProviderKind): boolean {
  return DRY_RUN_KINDS.has(kind);
}

export function tryReducer(s: TryState, a: TryAction): TryState {
  switch (a.type) {
    case 'submit':
      if (s.step !== 'form') return s;
      return a.dryRun
        ? { ...s, step: 'estimating', error: null }
        : { ...s, step: 'confirm', estimate: null, error: null };
    case 'estimated':
      return s.step === 'estimating' ? { ...s, step: 'estimate', estimate: a.estimate } : s;
    case 'run':
      return s.step === 'estimate' || s.step === 'confirm' ? { ...s, step: 'running', error: null } : s;
    case 'started':
      return s.step === 'running' ? { ...s, step: 'job', jobId: a.jobId, error: null } : s;
    case 'failed':
      if (s.step === 'estimating') return { ...s, step: 'form', error: a.error };
      if (s.step === 'running') return { ...s, step: s.estimate ? 'estimate' : 'confirm', error: a.error };
      return s;
    case 'back':
      return s.step === 'estimate' || s.step === 'confirm' ? { ...s, step: 'form', estimate: null, error: null } : s;
    case 'reset':
      return INITIAL;
  }
}

const KIND_ROW: Record<ProviderKind, string> = { image: 'Ảnh', video: 'Clip', tts: 'TTS', llm: 'LLM', stt: 'STT' };

/**
 * Bảng ước tính cho một lần thử: 1 việc × `units` lượt (đơn vị gốc của provider). Backend chưa định nghĩa đơn vị của
 * `cost` (adapter giả trả 0) nên không quy đổi ₫.
 */
export function estimateRows(e: TryEstimate): EstimateRow[] {
  return [{ kind: KIND_ROW[e.kind] ?? e.kind, quantity: 1, unitCredits: e.units ?? 1, unitVnd: null }];
}
