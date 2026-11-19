import { CornerDownLeft, Search } from 'lucide-react';
import { useMemo, useState, type KeyboardEvent } from 'react';
import { useNavigate } from 'react-router';
import type { SequenceInfo } from '../../api/types';
import { GLOBAL_NAV, PROJECT_NAV } from '../../app/nav';
import { cn } from '../../lib/cn';
import { foldVi } from '../../lib/format';
import { Modal } from '../ui/Modal';

export interface PaletteEntry {
  key: string;
  label: string;
  group: string;
  to: string;
  hint?: string;
}

/** Mục của bảng tìm: trang toàn cục, trang trong dự án, tập + Studio của tập (khớp không dấu). */
export function paletteEntries(base: string, sequences: SequenceInfo[] | undefined): PaletteEntry[] {
  const out: PaletteEntry[] = [];
  if (base) {
    for (const n of PROJECT_NAV) {
      out.push({ key: `p-${n.key}`, label: n.label, group: 'Đi tới', to: `${base}/${n.to}`, hint: n.hotkey ? `G ${n.hotkey.toUpperCase()}` : undefined });
    }
  }
  for (const n of GLOBAL_NAV) out.push({ key: `g-${n.key}`, label: n.label, group: 'Toàn app', to: n.to });
  for (const s of sequences ?? []) {
    const name = `Tập ${s.number}${s.title ? ` · ${s.title}` : ''}`;
    out.push({ key: `s-${s.id}`, label: name, group: 'Tập', to: `${base}/s/${s.id}` });
    out.push({ key: `st-${s.id}`, label: `Studio tập ${s.number}`, group: 'Tập', to: `${base}/studio/${s.id}` });
  }
  return out;
}

export function filterEntries(entries: PaletteEntry[], q: string): PaletteEntry[] {
  const needle = foldVi(q.trim());
  if (!needle) return entries.slice(0, 30);
  return entries.filter((e) => foldVi(`${e.label} ${e.group}`).includes(needle)).slice(0, 30);
}

/**
 * Bảng tìm `Ctrl K` tối thiểu của G0: tìm theo danh sách trang + tập. Bảng lệnh đầy đủ (hành động, clip, tài sản —
 * DESIGN §6.10) thay thế sau.
 */
export function RoutePalette({ open, onClose, entries }: { open: boolean; onClose: () => void; entries: PaletteEntry[] }) {
  const navigate = useNavigate();
  const [q, setQ] = useState('');
  const [sel, setSel] = useState(0);
  const shown = useMemo(() => filterEntries(entries, q), [entries, q]);

  const go = (e: PaletteEntry | undefined) => {
    if (!e) return;
    onClose();
    setQ('');
    navigate(e.to);
  };
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSel((i) => Math.min(shown.length - 1, i + 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSel((i) => Math.max(0, i - 1));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      go(shown[sel]);
    }
  };

  return (
    <Modal open={open} onClose={onClose} title="Tìm trang hoặc tập" size="md">
      <div className="grid gap-2">
        <label className="flex h-touch items-center gap-2 rounded-sm border border-line-3 bg-bg-2 px-3 md:h-10">
          <Search className="sr-ico text-fg-3" aria-hidden />
          <input
            data-autofocus
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setSel(0);
            }}
            onKeyDown={onKey}
            role="combobox"
            aria-expanded
            aria-controls="sr-palette-list"
            aria-activedescendant={shown[sel] ? `sr-pal-${shown[sel].key}` : undefined}
            aria-label="Tìm"
            placeholder="Gõ tên trang hoặc số tập (không cần dấu)"
            className="min-w-0 flex-1 bg-transparent text-md outline-none placeholder:text-fg-3 md:text-base"
          />
        </label>
        <ul id="sr-palette-list" role="listbox" aria-label="Kết quả" className="max-h-[50dvh] overflow-y-auto">
          {shown.length === 0 ? <li className="px-2 py-3 text-sm text-fg-2">Không có trang hoặc tập nào khớp.</li> : null}
          {shown.map((e, i) => (
            <li
              key={e.key}
              id={`sr-pal-${e.key}`}
              role="option"
              aria-selected={i === sel}
              onMouseEnter={() => setSel(i)}
              onClick={() => go(e)}
              className={cn(
                'flex min-h-touch cursor-pointer items-center gap-3 rounded-md px-2 text-sm md:min-h-9',
                i === sel ? 'bg-bg-3 text-fg-1' : 'text-fg-2',
              )}
            >
              <span className="min-w-0 flex-1 truncate">{e.label}</span>
              <span className="text-xs text-fg-3">{e.group}</span>
              {e.hint ? <kbd className="font-mono text-xs text-fg-3">{e.hint}</kbd> : null}
              {i === sel ? <CornerDownLeft className="h-3.5 w-3.5 text-fg-3" aria-hidden /> : null}
            </li>
          ))}
        </ul>
      </div>
    </Modal>
  );
}

const KEYS: { group: string; rows: [string, string][] }[] = [
  {
    group: 'Toàn app',
    rows: [
      ['Ctrl K', 'Tìm trang hoặc tập'],
      ['?', 'Bảng phím này'],
      ['.', 'Mở/đóng panel Hoạt động'],
      ['G rồi O · C · L · D · X · H', 'Tổng quan · Câu chuyện · Thư viện · Duyệt · Xuất · Hoạt động'],
      ['G rồi T · I', 'Tập (ma trận) · Cần bạn'],
      ['Esc', 'Đóng lớp trên cùng'],
    ],
  },
  {
    group: 'Trong một tập',
    rows: [
      ['Alt 1 … 8', 'Mở chặng 1–8 của dây chuyền'],
      ['[  ]', 'Tập trước / tập kế'],
    ],
  },
];

/** Bảng phím `?` — danh sách tối thiểu; phím riêng của Duyệt/Studio do màn đó bổ sung. */
export function KeysDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Modal open={open} onClose={onClose} title="Phím tắt" size="md">
      <div className="grid gap-4">
        {KEYS.map((g) => (
          <section key={g.group} className="grid gap-1">
            <h3 className="text-xs font-medium text-fg-3">{g.group}</h3>
            <dl className="grid grid-cols-[minmax(0,auto)_minmax(0,1fr)] gap-x-4 gap-y-1.5">
              {g.rows.map(([k, v]) => (
                <div key={k} className="contents">
                  <dt className="font-mono text-xs text-fg-1">{k}</dt>
                  <dd className="text-sm text-fg-2">{v}</dd>
                </div>
              ))}
            </dl>
          </section>
        ))}
        <p className="text-xs text-fg-3">Phím chữ không chạy khi đang gõ trong ô nhập. Trong Studio, G là lớp safe-zone.</p>
      </div>
    </Modal>
  );
}
