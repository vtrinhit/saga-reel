import { Check, CircleDashed, Diamond, History, LoaderCircle, Minus, TriangleAlert, type LucideIcon } from 'lucide-react';
import { useLayoutEffect, useRef } from 'react';
import { Link } from 'react-router';
import { cn, focusRing } from '../../lib/cn';
import { formatSeconds } from '../../lib/format';
import { stageDescription, stageHref, type Stage, type StageGate, type StageKey, type StageStatus } from '../../lib/pipeline';

const ICON: Record<StageStatus, LucideIcon> = {
  todo: CircleDashed,
  running: LoaderCircle,
  wait: Diamond,
  done: Check,
  failed: TriangleAlert,
  stale: History,
  skipped: Minus,
};

const TONE: Record<StageStatus, string> = {
  todo: 'text-fg-3',
  running: 'text-state-running',
  wait: 'text-accent',
  done: 'text-fg-2',
  failed: 'text-state-failed',
  stale: 'text-state-stale',
  skipped: 'text-fg-3 line-through',
};

export interface StageSpineProps {
  base: string;
  seqId: string;
  /** "Tập 12" */
  number: number | null;
  title: string;
  clips: number | null;
  durationMs: number | null;
  stages: Stage[];
  /** Chặng của màn đang mở. */
  activeKey: StageKey | null;
  loading?: boolean;
}

/** Hình thoi gate giữa hai chặng: đặc fg-2 = đã duyệt; viền đứt accent = chờ bạn; viền fg-3 = chưa tới. */
function GateDot({ gate }: { gate: StageGate | null }) {
  if (!gate) return <span className="h-px w-2.5 flex-none bg-line-2" aria-hidden />;
  const waiting = gate.status === 'waiting';
  const approved = gate.status === 'approved';
  return (
    <span className="grid h-4 w-4 flex-none place-items-center" title={`${gate.key} — ${waiting ? 'chờ bạn duyệt' : approved ? 'đã duyệt' : 'chưa tới'}`} aria-hidden>
      <svg viewBox="0 0 12 12" className="h-3 w-3">
        <path
          d="M6 1 11 6 6 11 1 6z"
          fill={approved ? 'rgb(var(--c-fg-2))' : 'none'}
          stroke={waiting ? 'rgb(var(--c-accent))' : approved ? 'rgb(var(--c-fg-2))' : 'rgb(var(--c-fg-4))'}
          strokeWidth="1.4"
          strokeDasharray={waiting ? '2 1.5' : undefined}
        />
      </svg>
    </span>
  );
}

/**
 * Thanh chặng của tập (DESIGN §6.1): tên tập + 8 chặng, gate giữa các chặng. Dính dưới topbar (768: dưới thanh tab),
 * cuộn ngang trong chính nó. Điện thoại: chỉ icon, chặng đang mở có chữ, tự cuộn tới giữa.
 */
export function StageSpine({ base, seqId, number, title, clips, durationMs, stages, activeKey, loading }: StageSpineProps) {
  const scroller = useRef<HTMLOListElement>(null);
  const name = number != null ? `Tập ${number}` : `Tập #${seqId}`;

  // Đưa chặng đang mở vào giữa khung cuộn (chỉ cuộn ngang trong thanh, không kéo trang).
  useLayoutEffect(() => {
    const el = scroller.current;
    const cur = el?.querySelector<HTMLElement>('[aria-current="page"]');
    if (!el || !cur || el.scrollWidth <= el.clientWidth) return;
    el.scrollLeft = cur.offsetLeft - el.clientWidth / 2 + cur.offsetWidth / 2;
  }, [activeKey, stages.length]);

  const sub = [
    clips != null ? `${clips} clip` : null,
    durationMs ? formatSeconds(Math.round(durationMs / 1000)) : null,
  ].filter(Boolean).join(' · ');

  return (
    <div
      data-spine
      className="sticky top-[calc(var(--sr-banner-h,0px)+var(--h-topbar)+var(--sr-tabs-h,0px))] z-sticky flex h-spine min-w-0 items-center gap-3 border-b border-line-1 bg-bg-1 pl-3 md:pl-4"
    >
      <div className="hidden min-w-0 max-w-[220px] flex-none sm:block">
        <p className="truncate text-sm font-semibold leading-tight">
          {name}
          {title ? <span className="font-semibold"> · {title}</span> : null}
        </p>
        {sub ? <p className="truncate text-2xs text-fg-3 tnum">{sub}</p> : null}
      </div>
      <p className="flex-none text-sm font-semibold sm:hidden">{name}</p>
      <nav aria-label={`Dây chuyền của ${name.toLowerCase()}`} aria-busy={loading || undefined} className="relative min-w-0 flex-1">
        <ol ref={scroller} className="relative flex h-spine items-center gap-1 overflow-x-auto pr-3 scrollbar-none">
          {stages.map((s, i) => {
            const Icon = ICON[s.status];
            const active = s.key === activeKey;
            return (
              <li key={s.key} className="flex flex-none items-center gap-1">
                <Link
                  to={stageHref(base, seqId, s.key)}
                  aria-current={active ? 'page' : undefined}
                  title={stageDescription(s)}
                  aria-label={stageDescription(s)}
                  className={cn(
                    'relative inline-flex h-control items-center gap-1.5 whitespace-nowrap rounded-md border border-transparent px-2 text-sm',
                    'hover:bg-bg-3 active:bg-bg-4',
                    active ? 'border-line-2 bg-bg-2 text-fg-1' : s.status === 'todo' || s.status === 'skipped' ? 'text-fg-3' : 'text-fg-1',
                    focusRing,
                    'focus-visible:-outline-offset-2',
                  )}
                >
                  <Icon
                    className={cn('h-3.5 w-3.5 flex-none', TONE[s.status], s.status === 'running' && 'sr-spin', s.status === 'todo' && 'text-fg-4')}
                    strokeWidth={1.75}
                    aria-hidden
                  />
                  <span className={cn(active ? 'inline' : 'hidden md:inline', s.status === 'skipped' && 'line-through')}>{s.label}</span>
                  {s.countLabel ? (
                    <span className={cn('text-xs font-semibold tnum', TONE[s.status], !active && 'hidden md:inline')}>{s.countLabel}</span>
                  ) : null}
                  {s.status === 'wait' ? (
                    <span className={cn('sr-pill h-5 items-center rounded-sm px-1.5 text-xs font-medium', active ? 'inline-flex' : 'hidden md:inline-flex')} data-status="wait">
                      chờ bạn
                    </span>
                  ) : null}
                </Link>
                {i < stages.length - 1 ? <GateDot gate={s.gate} /> : s.gate ? <GateDot gate={s.gate} /> : null}
              </li>
            );
          })}
        </ol>
      </nav>
    </div>
  );
}
