import { CircleAlert } from 'lucide-react';
import type { NextCostView } from '../../lib/activity';
import { cn } from '../../lib/cn';

/**
 * Khối "giá của bước kế tiếp" trong GateCard (DESIGN §6.3): lời dẫn · số lượng + lượt (lg mono) · ghi chú. Duyệt gate là
 * cho phép tiêu tiền, nên khối này luôn hiện — kể cả khi chưa có ước tính ("đang ước tính").
 */
export function NextCost({ view, loading, className }: { view: NextCostView; loading?: boolean; className?: string }) {
  return (
    <div className={cn('grid gap-1 rounded-md bg-bg-2 px-3 py-2.5', className)}>
      <p className="text-xs text-fg-2">{view.lead}</p>
      <p className="font-mono text-lg tnum text-fg-1">{view.amount ?? (loading ? 'đang ước tính…' : 'chưa ước tính được')}</p>
      {view.notes.length ? <p className="text-xs text-fg-2">{view.notes.join(' · ')}</p> : null}
      {view.overBudget ? (
        <p role="alert" className="sr-warnbox mt-1 flex items-start gap-2 rounded-sm px-2 py-1.5 text-xs">
          <CircleAlert className="mt-px h-3.5 w-3.5 flex-none text-warn" strokeWidth={1.75} aria-hidden />
          Vượt ngân sách tháng — duyệt thì việc tốn lượt sẽ đứng chờ ở trạng thái "vượt ngân sách".
        </p>
      ) : null}
    </div>
  );
}
