/**
 * Một ô của ma trận xuất (brief §3.7): checkbox "Chọn" + pill kết quả `export.check` + spec mono + IssueList + hành
 * động sửa. Ô là `div` (không lồng nút trong nhãn): label bọc checkbox, nút "Xem metadata" là nút thật riêng.
 */
import { Check, CircleAlert, Diamond, Languages, Scissors, TriangleAlert } from 'lucide-react';
import { Link } from 'react-router';
import { cn, focusRing } from '../../lib/cn';
import { issues, SOFT_CODES, verdict, verdictLabel, sizeLabel } from '../../lib/exportMatrix';
import type { CheckReport } from '../../lib/studioData';
import { shortTime } from '../../lib/timeline';
import { Button } from '../ui/Button';
import { IssueList } from './IssueList';

const PILL: Record<string, { cls: string; icon: typeof Check }> = {
  ok: { cls: 'text-ok bg-ok/[0.16]', icon: Check },
  warn: { cls: 'text-warn bg-warn/[0.16]', icon: CircleAlert },
  split: { cls: 'text-warn bg-warn/[0.16]', icon: Scissors },
  error: { cls: 'text-state-failed bg-state-failed/[0.16]', icon: TriangleAlert },
  unknown: { cls: 'text-fg-2 bg-bg-3', icon: Diamond },
};

export function ExportCell({ id, report, loading, error, checked, active, missingTranslations, translateHref, readOnly, onToggle, onActivate }: {
  id: string;
  report: CheckReport | null;
  loading: boolean;
  error: string | null;
  checked: boolean;
  active: boolean;
  missingTranslations: number;
  /** Màn Âm thanh ở đúng ngôn ngữ (dịch tay; dịch + lồng tiếng hàng loạt chưa có op). */
  translateHref?: string;
  readOnly?: boolean;
  onToggle: (v: boolean) => void;
  onActivate: () => void;
}) {
  const base = verdict(report);
  // Thiếu bản dịch thoại: ô ngôn ngữ phụ chưa xuất được đúng nghĩa dù kiểm tra kỹ thuật đạt.
  const untranslated = missingTranslations > 0 && (base === 'ok' || base === 'unknown');
  const v = untranslated ? 'warn' : base;
  const pill = PILL[v];
  const Icon = pill.icon;
  const info = report?.info;
  return (
    <div
      className={cn(
        'relative grid min-w-0 content-start gap-2 rounded-md border bg-bg-1 p-3',
        checked ? 'border-accent/60' : 'border-line-2',
        active && 'outline outline-2 outline-offset-[-2px] outline-line-3',
      )}
    >
      <div className="flex min-w-0 items-center justify-between gap-2">
        <label className="flex min-h-touch cursor-pointer items-center gap-2 text-sm md:min-h-0">
          <input
            id={id}
            type="checkbox"
            className={cn('size-4 accent-[rgb(var(--c-accent))]', focusRing)}
            checked={checked}
            disabled={readOnly}
            onChange={(e) => onToggle(e.target.checked)}
          />
          Chọn
        </label>
        <span className={cn('inline-flex h-5 items-center gap-1 whitespace-nowrap rounded-sm px-2 text-xs font-medium', pill.cls)}>
          <Icon className="size-3" aria-hidden />
          {loading ? 'Đang kiểm' : error ? 'Không kiểm được' : untranslated ? 'Thiếu dịch' : verdictLabel(v, report)}
        </span>
      </div>
      <p className="font-mono text-xs tabular-nums text-fg-2">
        {info?.duration_ms != null ? `${info.estimated ? '~' : ''}${shortTime(info.duration_ms)}` : '—'}
        {info?.size ? ` · ${info.estimated ? '~' : ''}${sizeLabel(info.size)}` : ''}
      </p>
      {missingTranslations > 0 ? (
        <p className="flex gap-1.5 text-xs text-warn">
          <Languages className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          {missingTranslations} câu thoại chưa dịch
        </p>
      ) : null}
      {error ? <p className="text-xs text-state-failed">{error}</p> : <IssueList items={issues(report).filter((i) => !SOFT_CODES.has(i.code))} compact />}
      <div className="flex flex-wrap gap-2">
        {missingTranslations > 0 && translateHref && !readOnly ? (
          <Link
            to={translateHref}
            className={cn(
              'inline-flex h-control flex-1 items-center justify-center gap-2 whitespace-nowrap rounded-md border border-line-2 bg-bg-2 px-3 text-sm font-medium hover:bg-bg-3',
              focusRing,
            )}
          >
            Dịch thoại
          </Link>
        ) : null}
        <Button variant="ghost" onClick={onActivate} aria-pressed={active} className="flex-1">
          Xem metadata
        </Button>
      </div>
    </div>
  );
}
