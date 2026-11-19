/**
 * Chỗ cắm cho Task M trên Tổng quan (H chỉ dựng ma trận). M thay phần thân hai component này — giữ nguyên tên + props:
 * - `RunToGateAction`: nút "Chạy tới gate kế tiếp" trong thanh lô (`pipeline.run` dry_run → EstimateDialog theo chặng
 *   → run). Bản H: nút tắt kèm lý do.
 * - `NeedsColumn`: cột/khối "Cần bạn" (GateCard/NeedItem). Bản H: liệt kê tập có gate đang chờ (đọc từ ma trận) +
 *   link trang Hoạt động.
 */
import { ChevronRight, Diamond, Play } from 'lucide-react';
import { Link } from 'react-router';
import { Button } from '../../components/ui/Button';
import type { MatrixRow } from '../../lib/overviewMatrix';

export interface RunToGateProps {
  sequenceIds: number[];
  projectId: number;
}

export function RunToGateAction({ sequenceIds }: RunToGateProps) {
  return (
    <Button icon={Play} disabled title="Chạy quy trình từ Tổng quan có ở bản sau; tạm chạy từng chặng trong tập." aria-describedby="run-gate-note">
      Chạy tới gate kế tiếp
      <span id="run-gate-note" className="sr-only">
        Chưa bật cho {sequenceIds.length} tập đã chọn.
      </span>
    </Button>
  );
}

export interface NeedsColumnProps {
  base: string;
  rows: MatrixRow[];
  limit?: number;
}

export function NeedsColumn({ base, rows, limit = 5 }: NeedsColumnProps) {
  const items = rows.flatMap((r) =>
    r.kind === 'seq'
      ? r.stages
          .filter((s) => s.status === 'wait' || s.status === 'failed')
          .map((s) => ({ seq: r.seq, stage: s }))
      : [],
  );
  return (
    <section aria-labelledby="needs-h" className="grid min-w-0 content-start gap-2">
      <div className="flex items-center justify-between">
        <h2 id="needs-h" className="text-sm font-semibold">Cần bạn</h2>
        <Link to={`${base}/activity`} className="rounded-sm text-sm font-medium hover:text-accent focus-visible:outline focus-visible:outline-focus focus-visible:outline-accent">
          Xem tất cả{items.length ? ` ${items.length}` : ''}
        </Link>
      </div>
      {items.length === 0 ? <p className="rounded-md border border-line-1 px-3 py-3 text-sm text-fg-2">Không có gate hay lỗi nào chờ bạn.</p> : null}
      <ul className="grid gap-2">
        {items.slice(0, limit).map(({ seq, stage }) => (
          <li key={`${seq.id}-${stage.key}`}>
            <Link
              to={`${base}/s/${seq.id}`}
              className="grid grid-cols-[32px_minmax(0,1fr)_auto] items-center gap-3 rounded-md border border-line-2 bg-bg-1 px-3 py-2.5 hover:bg-bg-3 focus-visible:outline focus-visible:outline-focus focus-visible:outline-offset-focus focus-visible:outline-accent"
            >
              <span
                className={
                  stage.status === 'wait'
                    ? 'grid h-8 w-8 place-items-center rounded-full border-2 border-dashed border-accent text-accent'
                    : 'grid h-8 w-8 place-items-center rounded-full bg-state-failed/15 text-state-failed'
                }
              >
                <Diamond className="h-3.5 w-3.5" aria-hidden />
              </span>
              <span className="grid min-w-0">
                <span className="truncate py-px text-sm font-medium">
                  {stage.status === 'wait' ? `Duyệt ${stage.label.toLowerCase()}` : `${stage.label}: ${stage.countLabel ?? 'lỗi'}`} · Tập {seq.number}
                </span>
                <span className="truncate py-px text-xs text-fg-2">{seq.title}</span>
              </span>
              <ChevronRight className="h-4 w-4 text-fg-3" aria-hidden />
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
