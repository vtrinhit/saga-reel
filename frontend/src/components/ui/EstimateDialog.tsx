import { CircleAlert, Play } from 'lucide-react';
import type { ReactNode } from 'react';
import { formatCount, formatCredits, formatVnd } from '../../lib/format';
import { Button } from './Button';
import { Checkbox } from './Checkbox';
import { Modal } from './Modal';

export interface EstimateRow {
  /** "Ảnh", "Clip", "TTS", "LLM" */
  kind: string;
  quantity: number;
  /** Lượt (đơn vị gốc của provider) cho mỗi đơn vị. */
  unitCredits: number;
  /** Quy đổi ₫ cho mỗi đơn vị, nếu biết. */
  unitVnd?: number | null;
}

export interface EstimateDialogProps {
  open: boolean;
  onClose: () => void;
  onRun: () => void;
  rows: EstimateRow[];
  /** Số việc sẽ chạy (cho nhãn nút). Mặc định = tổng số lượng. */
  jobCount?: number;
  /** Ngân sách tháng còn lại (lượt); null = chưa đặt ngân sách. */
  budgetRemaining?: number | null;
  /** Cảnh báo khả năng provider, vd "GrokTele không nhận ảnh tham chiếu thứ 2 — chỉ gửi ảnh đầu." */
  warnings?: ReactNode[];
  /** Tuỳ chọn "Không hỏi lại dưới 5 lượt". */
  skipBelow?: { checked: boolean; onChange: (v: boolean) => void; threshold?: number };
  running?: boolean;
  /** Đang mất kết nối: khoá nút trả phí. */
  offline?: boolean;
  title?: string;
}

/** Trước mọi lần chạy trả phí: bảng theo loại, tổng lượt (2xl mono) và ≈ ₫, ngân sách còn lại, băng warn khi vượt. */
export function EstimateDialog({
  open,
  onClose,
  onRun,
  rows,
  jobCount,
  budgetRemaining = null,
  warnings = [],
  skipBelow,
  running,
  offline,
  title = 'Ước tính chi phí',
}: EstimateDialogProps) {
  const totalCredits = rows.reduce((s, r) => s + r.quantity * r.unitCredits, 0);
  const vndKnown = rows.every((r) => r.unitVnd != null);
  const totalVnd = vndKnown ? rows.reduce((s, r) => s + r.quantity * (r.unitVnd ?? 0), 0) : null;
  const jobs = jobCount ?? rows.reduce((s, r) => s + r.quantity, 0);
  const overBudget = budgetRemaining != null && totalCredits > budgetRemaining;

  return (
    <Modal
      open={open}
      onClose={running ? undefined : onClose}
      title={title}
      actions={
        <>
          <Button onClick={onClose} disabled={running}>
            Huỷ
          </Button>
          <Button variant="primary" icon={Play} loading={running} disabled={offline} onClick={onRun}>
            {`Chạy ${formatCount(jobs)} việc · ${formatCredits(totalCredits)}`}
          </Button>
        </>
      }
    >
      <div className="grid gap-3">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-line-1 text-left text-xs text-fg-2">
              <th className="py-1.5 font-normal">Loại</th>
              <th className="py-1.5 text-right font-normal">Số lượng</th>
              <th className="py-1.5 text-right font-normal">Đơn giá</th>
              <th className="py-1.5 text-right font-normal">Lượt</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.kind} className="border-b border-line-1">
                <td className="py-1.5">{r.kind}</td>
                <td className="whitespace-nowrap py-1.5 text-right font-mono tnum">{formatCount(r.quantity)}</td>
                <td className="whitespace-nowrap py-1.5 text-right font-mono tnum">× {formatCount(r.unitCredits)}</td>
                <td className="whitespace-nowrap py-1.5 text-right font-mono tnum">
                  {formatCount(r.quantity * r.unitCredits)}
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <td className="pt-3 text-fg-2">Tổng</td>
              <td colSpan={3} className="pt-3 text-right">
                <span className="block font-mono text-2xl tnum">{formatCredits(totalCredits)}</span>
                {totalVnd != null ? <span className="font-mono text-xs text-fg-2 tnum">{formatVnd(totalVnd)}</span> : null}
              </td>
            </tr>
          </tfoot>
        </table>

        {budgetRemaining != null ? (
          <p className="text-xs text-fg-2">
            Ngân sách tháng còn <span className="font-mono tnum text-fg-1">{formatCredits(budgetRemaining)}</span>
          </p>
        ) : null}

        {overBudget ? (
          <div role="alert" className="sr-warnbox flex items-start gap-2 rounded-sm px-2.5 py-2 text-sm">
            <CircleAlert className="sr-ico mt-0.5 text-warn" aria-hidden />
            <span>Vượt ngân sách tháng {formatCredits(totalCredits - (budgetRemaining ?? 0))}.</span>
          </div>
        ) : null}

        {warnings.map((w, i) => (
          <div key={i} className="sr-warnbox flex items-start gap-2 rounded-sm px-2.5 py-2 text-sm">
            <CircleAlert className="sr-ico mt-0.5 text-warn" aria-hidden />
            <span className="min-w-0">{w}</span>
          </div>
        ))}

        {offline ? (
          <p className="text-xs text-state-failed">Đang mất kết nối với máy nhà — chưa chạy được việc trả phí.</p>
        ) : null}

        {skipBelow ? (
          <Checkbox
            label={`Không hỏi lại dưới ${skipBelow.threshold ?? 5} lượt`}
            checked={skipBelow.checked}
            onChange={(e) => skipBelow.onChange(e.target.checked)}
          />
        ) : null}
      </div>
    </Modal>
  );
}
