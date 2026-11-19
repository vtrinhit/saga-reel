/** Danh sách lỗi/cảnh báo của `export.check` — icon + chữ + màu (bỏ màu vẫn đọc được), gợi ý sửa ở dòng phụ. */
import { CircleAlert, Info, TriangleAlert } from 'lucide-react';
import { SOFT_CODES } from '../../lib/exportMatrix';
import type { Issue } from '../../lib/studioData';
import { cn } from '../../lib/cn';

export function IssueList({ items, compact }: { items: (Issue & { level: 'error' | 'warn' })[]; compact?: boolean }) {
  if (!items.length) return null;
  const shown = compact ? items.slice(0, 3) : items;
  return (
    <ul className="grid gap-1">
      {shown.map((i, n) => {
        const soft = SOFT_CODES.has(i.code);
        const Icon = i.level === 'error' ? TriangleAlert : soft ? Info : CircleAlert;
        return (
          <li key={`${i.code}-${n}`} className={cn('flex gap-1.5 text-xs', i.level === 'error' ? 'text-state-failed' : soft ? 'text-fg-2' : 'text-warn')}>
            <Icon className="mt-0.5 size-3.5 shrink-0" aria-hidden />
            <span className="min-w-0">
              <span className="sr-only">{i.level === 'error' ? 'Lỗi: ' : 'Cảnh báo: '}</span>
              {i.message}
              {!compact && i.hint ? <span className="block text-fg-2">{i.hint}</span> : null}
            </span>
          </li>
        );
      })}
      {compact && items.length > shown.length ? <li className="text-xs text-fg-2">+{items.length - shown.length} vấn đề khác</li> : null}
    </ul>
  );
}
