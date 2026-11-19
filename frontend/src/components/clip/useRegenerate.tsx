/**
 * Tạo lại trả phí: `clip.regenerate dry_run` → EstimateDialog (bảng theo phần, ngân sách còn) → chạy thật. Dưới ngưỡng
 * "Không hỏi lại dưới n lượt" thì chạy thẳng. Backend chưa có khoá cài đặt cho ngưỡng → lưu cục bộ (mặc định tắt, 5 lượt).
 * Không cập nhật lạc quan (DESIGN §7.3): card chỉ chuyển "Chờ #n" khi server nhận việc.
 */
import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useState, type ReactNode } from 'react';
import { ApiError } from '../../api/client';
import { callOp } from '../../api/ops';
import { useConnection } from '../../stores/connection';
import { toast } from '../../stores/toast';
import type { RegenerateResult } from '../../lib/episodeTypes';
import { EstimateDialog, type EstimateRow } from '../ui/EstimateDialog';

export const SKIP_THRESHOLD = 5;
const LS_KEY = 'sr.regen.skipBelow';

function readSkip(): boolean {
  try {
    return localStorage.getItem(LS_KEY) === '1';
  } catch {
    return false;
  }
}
function writeSkip(v: boolean) {
  try {
    localStorage.setItem(LS_KEY, v ? '1' : '0');
  } catch {
    /* bỏ qua */
  }
}

export interface RegenRequest {
  ids?: number[];
  selector?: { sequence_id: number; filter?: Record<string, unknown> };
  part: 'image' | 'video' | 'all';
  overrides?: Record<string, unknown>;
  /** Nhãn cho toast: "Clip 18", "12 clip". */
  label: string;
  /** Gọi sau khi server nhận việc (vd. sang clip kế ở màn Duyệt). */
  onQueued?: (r: RegenerateResult) => void;
}

const PART_LABEL: Record<string, string> = { image: 'Ảnh', video: 'Video', still_motion: 'Ảnh động' };

export function estimateRows(r: RegenerateResult): EstimateRow[] {
  return Object.entries(r.estimate.parts).map(([k, v]) => ({
    kind: `${PART_LABEL[k] ?? k}${v.connection ? ` · ${v.connection}` : ''}`,
    quantity: v.jobs,
    unitCredits: v.jobs ? v.units / v.jobs : 0,
  }));
}

export function budgetRemaining(r: RegenerateResult): number | null {
  let rem: number | null = null;
  for (const v of Object.values(r.estimate.parts)) {
    const b = v.budget;
    if (b?.enabled && b.remaining != null) rem = rem == null ? b.remaining : Math.min(rem, b.remaining);
  }
  return rem;
}

export function errText(e: unknown): string {
  if (e instanceof ApiError) return e.hint ? `${e.detail} ${e.hint}` : e.detail;
  return e instanceof Error ? e.message : 'Lỗi không rõ.';
}

export function useRegenerate(seqId: number | null): { request: (r: RegenRequest) => Promise<void>; dialog: ReactNode } {
  const qc = useQueryClient();
  const offline = useConnection((s) => s.state === 'offline');
  const [pending, setPending] = useState<{ req: RegenRequest; est: RegenerateResult } | null>(null);
  const [running, setRunning] = useState(false);
  const [skip, setSkip] = useState(readSkip);

  const body = (req: RegenRequest, dry: boolean) => ({
    ids: req.ids ?? null,
    selector: req.selector ?? null,
    part: req.part,
    overrides: req.overrides ?? {},
    dry_run: dry,
  });

  const run = useCallback(
    async (req: RegenRequest) => {
      setRunning(true);
      try {
        const r = (await callOp('clip.regenerate', body(req, false) as never)) as unknown as RegenerateResult;
        const n = r.job_ids.length;
        const units = Object.values(r.estimate.parts).reduce((s, p) => s + p.units, 0);
        toast({
          message: n
            ? `Đã xếp tạo lại ${req.label} · ${units.toLocaleString('vi-VN')} lượt`
            : `Không có việc nào cho ${req.label}${r.skipped[0] ? `: ${r.skipped[0].message}` : '.'}`,
          tone: n ? 'success' : 'info',
          action: n
            ? {
                label: 'Huỷ việc',
                onClick: () => {
                  for (const id of r.job_ids) void callOp('job.cancel', { ids: [id] }).catch(() => undefined);
                },
              }
            : undefined,
        });
        req.onQueued?.(r);
        if (seqId != null) void qc.invalidateQueries({ queryKey: ['episode', seqId] });
        setPending(null);
      } catch (e) {
        toast({ message: `Không xếp được việc tạo lại: ${errText(e)}`, tone: 'error' });
      } finally {
        setRunning(false);
      }
    },
    [qc, seqId],
  );

  const request = useCallback(
    async (req: RegenRequest) => {
      try {
        const est = (await callOp('clip.regenerate', body(req, true) as never)) as unknown as RegenerateResult;
        const total = Object.values(est.estimate.parts).reduce((s, p) => s + p.units, 0);
        const rem = budgetRemaining(est);
        if (readSkip() && total < SKIP_THRESHOLD && (rem == null || total <= rem) && !offline) {
          await run(req);
          return;
        }
        setPending({ req, est });
      } catch (e) {
        toast({ message: `Không ước tính được chi phí: ${errText(e)}`, tone: 'error' });
      }
    },
    [offline, run],
  );

  const dialog = pending ? (
    <EstimateDialog
      open
      title={`Tạo lại ${pending.req.label}`}
      onClose={() => setPending(null)}
      onRun={() => void run(pending.req)}
      rows={estimateRows(pending.est)}
      jobCount={pending.est.estimate.jobs}
      budgetRemaining={budgetRemaining(pending.est)}
      warnings={[
        ...pending.est.warnings,
        ...pending.est.skipped.slice(0, 3).map((s) => `Bỏ qua clip #${s.clip_id}: ${s.message}`),
      ]}
      skipBelow={{
        checked: skip,
        threshold: SKIP_THRESHOLD,
        onChange: (v) => {
          setSkip(v);
          writeSkip(v);
        },
      }}
      running={running}
      offline={offline}
    />
  ) : null;

  return { request, dialog };
}
