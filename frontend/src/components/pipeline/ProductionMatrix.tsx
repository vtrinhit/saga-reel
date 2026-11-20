import { useQueries } from '@tanstack/react-query';
import { useVirtualizer } from '@tanstack/react-virtual';
import { Check, ChevronRight, CircleDashed, Diamond, History, LoaderCircle, Minus, TriangleAlert, type LucideIcon } from 'lucide-react';
import { memo } from 'react';
import { Link } from 'react-router';
import { callOp } from '../../api/ops';
import type { SequenceInfo } from '../../api/types';
import { cn } from '../../lib/cn';
import type { CostSummary } from '../../lib/episodeTypes';
import { cellText, type MatrixRow } from '../../lib/overviewMatrix';
import { STAGE_DEFS, stageDescription, stageHref, type Stage, type StageStatus } from '../../lib/pipeline';
import { relativeFromServer } from '../../lib/time';

const ICON: Record<StageStatus, LucideIcon> = {
  todo: Minus, running: LoaderCircle, wait: Diamond, done: Check, failed: TriangleAlert, stale: History, skipped: CircleDashed,
};
const TONE: Record<StageStatus, string> = {
  todo: 'text-fg-3', running: 'text-state-running', wait: 'text-accent', done: 'text-fg-2', failed: 'text-state-failed',
  stale: 'text-state-stale', skipped: 'text-fg-3 line-through',
};

export const MATRIX_ROW_H = 40;
const GROUP_H = 32;
/** Tập 200 · (ô chặng + gate 18) × 8 · Chi phí · Sửa gần nhất */
export const MATRIX_COLS = `180px repeat(8, minmax(92px,1fr) 14px) 96px 120px`;

/** Một ô chặng (DESIGN §6.2): icon 14 + chữ xs; chờ bạn = vòng đứt accent; chưa tới = "—" + sr-only. */
export const MatrixCell = memo(function MatrixCell({ stage, href }: { stage: Stage; href: string }) {
  const Icon = ICON[stage.status];
  const desc = stageDescription(stage);
  if (stage.status === 'todo') {
    return (
      <span className="relative px-2 text-xs text-fg-3" title={desc}>
        —<span className="sr-only">{desc}</span>
      </span>
    );
  }
  return (
    <Link
      to={href}
      title={desc}
      aria-label={desc}
      className={cn(
        'relative flex h-7 min-w-0 items-center gap-1.5 rounded-sm px-2 text-xs hover:bg-bg-3 focus-visible:outline focus-visible:outline-focus focus-visible:outline-offset-focus focus-visible:outline-accent',
        TONE[stage.status],
        stage.status === 'wait' && 'w-fit rounded-full border border-dashed border-accent',
      )}
    >
      <Icon className={cn('h-3.5 w-3.5 flex-none', stage.status === 'running' && 'sr-spin')} strokeWidth={1.75} aria-hidden />
      {stage.status === 'running' && stage.progress != null ? (
        <span className="h-1 w-11 flex-none overflow-hidden rounded-full bg-bg-3" aria-hidden>
          <span className="block h-full origin-left bg-state-running" style={{ transform: `scaleX(${stage.progress / 100})` }} />
        </span>
      ) : null}
      <span className="truncate tnum">{cellText(stage)}</span>
    </Link>
  );
});

function GateDot({ stage }: { stage: Stage }) {
  if (!stage.gate) return <span aria-hidden />;
  const wait = stage.gate.status === 'waiting';
  return (
    <span className="grid place-items-center" title={wait ? 'Gate chờ bạn duyệt' : 'Gate'}>
      <Diamond className={cn('h-2.5 w-2.5', wait ? 'text-accent' : stage.gate.status === 'approved' ? 'fill-fg-2 text-fg-2' : 'text-fg-3')} strokeWidth={wait ? 2 : 1.5} aria-hidden />
    </span>
  );
}

/** Số tập mỗi lần gọi `sequence.cost_summary(sequence_ids)` — chia theo vị trí hàng nên khoá query ổn định khi cuộn. */
export const COST_BUCKET = 50;

/** Chia id tập (theo thứ tự hàng) thành nhóm `size`; trả nhóm chứa ít nhất một id đang hiện. */
export function costBuckets(ids: number[], visible: Set<number>, size = COST_BUCKET): number[][] {
  const out: number[][] = [];
  for (let i = 0; i < ids.length; i += size) {
    const chunk = ids.slice(i, i + size);
    if (chunk.some((id) => visible.has(id))) out.push(chunk);
  }
  return out;
}

/** Chi phí thật của các tập đang hiện: một lần gọi cho mỗi nhóm 50 tập (thay vì một lần mỗi hàng). */
function useVisibleCosts(ids: number[], visible: Set<number>): Map<number, CostSummary | 'error'> {
  const buckets = costBuckets(ids, visible);
  const qs = useQueries({
    queries: buckets.map((chunk) => ({
      queryKey: ['overview', 'cost', chunk.join(',')],
      queryFn: async () =>
        ((await callOp('sequence.cost_summary', { sequence_ids: chunk, per_clip: false })) as unknown as {
          items: (CostSummary & { sequence_id: number })[];
        }).items,
      staleTime: 60_000,
      retry: false,
    })),
  });
  const out = new Map<number, CostSummary | 'error'>();
  qs.forEach((q, i) => {
    if (q.isError) for (const id of buckets[i]) out.set(id, 'error');
    for (const c of q.data ?? []) out.set(c.sequence_id, c);
  });
  return out;
}

function CostCell({ cost }: { cost: CostSummary | 'error' | undefined }) {
  const c = cost === 'error' ? undefined : cost;
  return (
    <span role="cell" className="px-2 text-right font-mono text-xs text-fg-2 tnum" title={c ? `${c.takes} take · ${c.retries} lần tạo lại` : undefined}>
      {c ? `${c.total.toLocaleString('vi-VN')} lượt` : cost === 'error' ? '—' : '…'}
    </span>
  );
}

export interface ProductionMatrixProps {
  rows: MatrixRow[];
  base: string;
  selected: Set<number>;
  onToggle: (seq: SequenceInfo, index: number, shift: boolean) => void;
  onToggleGroup: (groupId: number | null) => void;
  collapsed: Set<number | null>;
  scrollRef: React.RefObject<HTMLDivElement | null>;
}

/**
 * ProductionMatrix (DESIGN §4.2, §6.2): khung `role=region` cuộn hai chiều, header dính trên, cột Tập dính trái, hàng
 * 40px, hàng nhóm (arc) 32px thu gọn được; ảo hoá hàng (nhiều trăm tập vẫn mượt).
 */
export function ProductionMatrix({ rows, base, selected, onToggle, onToggleGroup, collapsed, scrollRef }: ProductionMatrixProps) {
  const v = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: (i) => (rows[i]?.kind === 'group' ? GROUP_H : MATRIX_ROW_H),
    overscan: 10,
    paddingStart: 36,
    useFlushSync: false,
    getItemKey: (i) => rows[i]?.key ?? i,
  });
  const seqRows = rows.filter((r) => r.kind === 'seq');
  const items = v.getVirtualItems();
  const visible = new Set<number>();
  for (const it of items) {
    const r = rows[it.index];
    if (r?.kind === 'seq') visible.add(r.seq.id);
  }
  const costs = useVisibleCosts(
    seqRows.map((r) => (r.kind === 'seq' ? r.seq.id : 0)),
    visible,
  );
  return (
    <div
      ref={scrollRef}
      role="region"
      aria-label="Ma trận tập × chặng"
      tabIndex={0}
      className="relative max-h-[min(70dvh,calc(100dvh-var(--sr-top)-240px))] min-h-[200px] min-w-0 overflow-auto overscroll-contain focus-visible:outline focus-visible:outline-focus focus-visible:-outline-offset-2 focus-visible:outline-accent"
    >
      <div role="table" aria-rowcount={seqRows.length + 1} className="relative min-w-[1240px]" style={{ height: v.getTotalSize() }}>
        <div role="row" className="sticky top-0 z-sticky grid h-9 items-center whitespace-nowrap border-b border-line-2 bg-bg-1 text-xs text-fg-2" style={{ gridTemplateColumns: MATRIX_COLS }}>
          <span role="columnheader" className="sticky left-0 z-sticky h-full bg-bg-1 px-3 leading-9">Tập</span>
          {STAGE_DEFS.map((d) => (
            <span key={d.key} role="columnheader" className="col-span-2 px-2">{d.label}</span>
          ))}
          <span role="columnheader" className="px-2 text-right">Chi phí</span>
          <span role="columnheader" className="px-2">Sửa gần nhất</span>
        </div>
        {items.map((it) => {
          const r = rows[it.index];
          if (r.kind === 'group') {
            const open = !collapsed.has(r.groupId);
            return (
              <div key={it.key} role="row" className="absolute inset-x-0 top-0 border-b border-line-1 bg-bg-0" style={{ transform: `translateY(${it.start}px)`, height: GROUP_H }}>
                <button
                  type="button"
                  aria-expanded={open}
                  onClick={() => onToggleGroup(r.groupId)}
                  className="sticky left-0 flex h-full items-center gap-1.5 px-3 text-sm font-semibold hover:text-accent focus-visible:outline focus-visible:outline-focus focus-visible:-outline-offset-2 focus-visible:outline-accent"
                >
                  <ChevronRight className={cn('h-4 w-4 transition-transform duration-fast', open && 'rotate-90')} aria-hidden />
                  {r.title}
                  <span className="font-normal text-fg-2">· {r.count} tập</span>
                </button>
              </div>
            );
          }
          const s = r.seq;
          const sel = selected.has(s.id);
          return (
            <div
              key={it.key}
              role="row"
              aria-selected={sel}
              className={cn('group absolute inset-x-0 top-0 grid items-center border-b border-line-1', sel ? 'bg-bg-3' : 'hover:bg-bg-2')}
              style={{ transform: `translateY(${it.start}px)`, height: MATRIX_ROW_H, gridTemplateColumns: MATRIX_COLS }}
            >
              <span role="rowheader" className={cn('sticky left-0 z-[1] flex h-full min-w-0 items-center gap-2 border-r border-line-1 px-3', sel ? 'bg-bg-3' : 'bg-bg-1 group-hover:bg-bg-2')}>
                <input
                  type="checkbox"
                  checked={sel}
                  aria-label={`Chọn tập ${s.number}`}
                  onClick={(e) => onToggle(s, it.index, e.shiftKey)}
                  onChange={() => undefined}
                  className="h-4 w-4 flex-none accent-[rgb(var(--c-accent))]"
                />
                <span className="flex-none font-mono text-xs text-fg-2 tnum">{String(s.number).padStart(2, '0')}</span>
                <Link
                  to={`${base}/s/${s.id}`}
                  className="min-w-0 truncate py-px text-sm hover:text-accent focus-visible:outline focus-visible:outline-focus focus-visible:outline-accent"
                >
                  {s.title}
                </Link>
              </span>
              {r.stages.map((st) => (
                <span key={st.key} role="cell" className="contents">
                  <span className="min-w-0">
                    <MatrixCell stage={st} href={stageHref(base, s.id, st.key)} />
                  </span>
                  <GateDot stage={st} />
                </span>
              ))}
              <CostCell cost={costs.get(s.id)} />
              <span role="cell" className="truncate px-2 text-xs text-fg-2">{s.updated_at ? relativeFromServer(s.updated_at) : '—'}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** Điện thoại (< 768): danh sách tập hai dòng — tên + chặng hiện tại (DESIGN §4.2). */
export function EpisodeList({
  rows,
  base,
}: {
  rows: MatrixRow[];
  base: string;
}) {
  return (
    <ul className="grid gap-px overflow-hidden rounded-md border border-line-2 bg-line-1">
      {rows.map((r) => {
        if (r.kind === 'group') {
          return (
            <li key={r.key} className="bg-bg-0 px-3 py-2 text-sm font-semibold">
              {r.title} <span className="font-normal text-fg-2">· {r.count} tập</span>
            </li>
          );
        }
        const cur = r.stages.find((s) => s.status === 'wait') ?? r.stages.find((s) => s.status !== 'done' && s.status !== 'skipped') ?? r.stages[r.stages.length - 1];
        const Icon = ICON[cur.status];
        return (
          <li key={r.key} className="bg-bg-1">
            <Link to={stageHref(base, r.seq.id, cur.key)} className="grid min-h-16 grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-3 px-3 py-2 hover:bg-bg-3 focus-visible:outline focus-visible:outline-focus focus-visible:-outline-offset-2 focus-visible:outline-accent">
              <span className="row-span-2 font-mono text-xs text-fg-2 tnum">{String(r.seq.number).padStart(2, '0')}</span>
              <span className="truncate py-px text-md">{r.seq.title}</span>
              <ChevronRight className="row-span-2 h-4 w-4 text-fg-3" aria-hidden />
              <span className={cn('flex min-w-0 items-center gap-1.5 text-xs', TONE[cur.status], cur.status === 'wait' && 'w-fit rounded-full border border-dashed border-accent px-2')}>
                <Icon className="h-3.5 w-3.5 flex-none" aria-hidden />
                <span className="truncate">{cur.label} · {cellText(cur)}</span>
              </span>
            </Link>
          </li>
        );
      })}
    </ul>
  );
}
