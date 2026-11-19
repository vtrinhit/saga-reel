import { ChevronDown, ChevronLeft, ChevronRight, Rows3 } from 'lucide-react';
import { useLocation, useNavigate } from 'react-router';
import type { SequenceInfo } from '../../api/types';
import { cn, focusRing } from '../../lib/cn';
import { Menu, type MenuItem } from '../ui/Menu';

/** Đổi tập trong cùng công cụ: `s/12/audio` → `s/13/audio`, `review?seq=12&part=video` → `?seq=13&part=video`. */
export function swapSeqInLocation(pathname: string, search: string, from: string, to: string | number): string {
  const re = new RegExp(`/(s|studio)/${from}(?=/|$)`);
  if (re.test(pathname)) return pathname.replace(re, `/$1/${to}`) + search;
  const q = new URLSearchParams(search);
  q.set('seq', String(to));
  return `${pathname}?${q.toString()}`;
}

/** Tập trước/kế theo số tập trong danh sách. */
export function neighbours(list: SequenceInfo[] | undefined, seqId: string): { prev: SequenceInfo | null; next: SequenceInfo | null } {
  const items = [...(list ?? [])].sort((a, b) => a.number - b.number || a.id - b.id);
  const i = items.findIndex((s) => String(s.id) === seqId);
  if (i < 0) return { prev: null, next: null };
  return { prev: items[i - 1] ?? null, next: items[i + 1] ?? null };
}

const epName = (s: Pick<SequenceInfo, 'number' | 'title'>) => `Tập ${s.number}${s.title ? ` · ${s.title}` : ''}`;

export interface EpisodeSwitcherProps {
  base: string;
  seqId: string;
  sequence: SequenceInfo | undefined;
  sequences: SequenceInfo[] | undefined;
  className?: string;
}

/** "Tập 12 ▾": menu tập trước / tập kế (giữ công cụ đang mở) + "Tất cả tập" (ma trận Tổng quan). Phím `[` `]`. */
export function EpisodeSwitcher({ base, seqId, sequence, sequences, className }: EpisodeSwitcherProps) {
  const navigate = useNavigate();
  const { pathname, search } = useLocation();
  const { prev, next } = neighbours(sequences, seqId);
  const label = sequence ? `Tập ${sequence.number}` : `Tập #${seqId}`;

  const items: MenuItem[] = [];
  if (prev) items.push({ key: 'prev', label: `Trước: ${epName(prev)}`, icon: ChevronLeft, onSelect: () => navigate(swapSeqInLocation(pathname, search, seqId, prev.id)) });
  if (next) items.push({ key: 'next', label: `Kế: ${epName(next)}`, icon: ChevronRight, onSelect: () => navigate(swapSeqInLocation(pathname, search, seqId, next.id)) });
  items.push({ key: 'all', label: 'Tất cả tập', icon: Rows3, onSelect: () => navigate(`${base}/overview`) });

  return (
    <Menu
      label="Đổi tập"
      items={items}
      trigger={(p) => (
        <button
          {...p}
          type="button"
          aria-label={`${sequence ? epName(sequence) : label} — đổi tập`}
          className={cn('inline-flex h-control min-w-0 flex-none items-center gap-1 whitespace-nowrap rounded-md px-2 text-sm font-semibold hover:bg-bg-3', focusRing, className)}
        >
          <span className="tnum">{label}</span>
          <ChevronDown className="sr-ico text-fg-2" aria-hidden />
        </button>
      )}
    />
  );
}
