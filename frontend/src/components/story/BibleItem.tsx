import { Check, ChevronDown, Lock, LockOpen, X } from 'lucide-react';
import { useId } from 'react';
import { cn, focusRing } from '../../lib/cn';
import { bibleMeta, type BibleEvidence, type BibleItem as Item } from '../../lib/story';
import { Button } from '../ui/Button';
import { StatusPill } from '../ui/StatusPill';
import { EvidenceQuote } from './EvidenceQuote';

export interface BibleItemProps {
  item: Item;
  /** Bằng chứng đã tải (bible.get / bible.search); chưa có → chỉ hiện số. */
  evidence?: BibleEvidence[];
  expanded: boolean;
  onToggle: () => void;
  viewer?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onReject: () => void;
  onLock: (locked: boolean) => void;
  seqNumber?: (id: number) => number | null;
}

/**
 * Một mục Story Bible: tóm tắt (sm 600) + meta (loại · từ tập n) + bằng chứng đầu tiên. Mục chờ xác nhận có pill
 * `wait` + hai nút Bỏ / Xác nhận; mục đã khoá ("nhãn nhân thiết" — LLM không được sửa) có nút khoá.
 */
export function BibleItem({ item, evidence, expanded, onToggle, viewer, busy, onConfirm, onReject, onLock, seqNumber }: BibleItemProps) {
  const detailId = useId();
  const first = evidence?.[0];
  const more = (evidence?.length ?? item.evidence_count) - (first ? 1 : 0);
  return (
    <li className="grid gap-1.5 border-t border-line-1 px-3 py-2.5 first:border-t-0">
      <div className="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-2">
        <div className="min-w-0">
          <p className={cn('text-sm font-semibold', item.status === 'rejected' && 'text-fg-3 line-through')}>{item.summary}</p>
          <p className="text-xs text-fg-2">
            {bibleMeta(item)} <span className="font-mono text-fg-3">{item.key}</span>
          </p>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-1">
          {item.status === 'pending' ? (
            viewer ? (
              <StatusPill status="wait" label="chờ xác nhận" />
            ) : (
              <>
                <Button variant="ghost" icon={X} className="text-xs" disabled={busy} onClick={onReject}>
                  Bỏ
                </Button>
                <Button icon={Check} className="text-xs" disabled={busy} onClick={onConfirm}>
                  Xác nhận
                </Button>
              </>
            )
          ) : item.status === 'rejected' ? (
            <span className="text-xs text-fg-3">đã bỏ</span>
          ) : viewer ? (
            item.locked ? (
              <span className="inline-flex items-center gap-1 text-xs text-fg-2">
                <Lock className="h-3 w-3" strokeWidth={1.75} aria-hidden />
                khoá
              </span>
            ) : null
          ) : (
            <Button
              variant="ghost"
              icon={item.locked ? Lock : LockOpen}
              className={cn('text-xs', item.locked ? 'text-fg-1' : 'text-fg-3')}
              aria-pressed={item.locked}
              disabled={busy}
              onClick={() => onLock(!item.locked)}
              title={item.locked ? 'Đang khoá: LLM không được sửa mục này. Bấm để mở khoá.' : 'Khoá để LLM không được sửa mục này.'}
            >
              {item.locked ? 'khoá' : 'Khoá'}
            </Button>
          )}
        </div>
      </div>
      {first ? <EvidenceQuote evidence={first} seqNumber={seqNumber} /> : null}
      {item.evidence_count > 0 && (more > 0 || !evidence) ? (
        <button
          type="button"
          aria-expanded={expanded}
          aria-controls={detailId}
          onClick={onToggle}
          className={cn('inline-flex items-center gap-1 justify-self-start rounded-sm text-xs text-fg-2 hover:text-fg-1', focusRing)}
        >
          {expanded ? 'Ẩn bằng chứng' : evidence ? `Thêm ${more} bằng chứng` : `${item.evidence_count} bằng chứng`}
          <ChevronDown className={cn('h-3.5 w-3.5 transition-transform duration-fast', expanded && 'rotate-180')} strokeWidth={1.75} aria-hidden />
        </button>
      ) : null}
      {expanded ? (
        <div id={detailId} className="grid gap-1.5">
          {evidence ? (
            evidence.slice(1).map((e) => <EvidenceQuote key={e.id} evidence={e} seqNumber={seqNumber} />)
          ) : (
            <p className="text-xs text-fg-3">Đang tải bằng chứng…</p>
          )}
        </div>
      ) : null}
    </li>
  );
}
