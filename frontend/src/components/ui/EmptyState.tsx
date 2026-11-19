import type { LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import { cn } from '../../lib/cn';

export interface EmptyStateProps {
  icon: LucideIcon;
  /** Một câu nói đang trống gì. */
  title: string;
  /** Câu gợi ý làm tiếp (tuỳ chọn). */
  description?: ReactNode;
  /** Một nút làm tiếp. */
  action?: ReactNode;
  className?: string;
}

/** Icon 32 fg-3, một câu, một nút làm tiếp (DESIGN §5.5). */
export function EmptyState({ icon: Icon, title, description, action, className }: EmptyStateProps) {
  return (
    <div className={cn('grid justify-items-start gap-2', className)}>
      <Icon className="h-8 w-8 text-fg-3" strokeWidth={1.75} aria-hidden />
      <p className="text-md font-medium">{title}</p>
      {description ? <p className="max-w-prose text-sm text-fg-2">{description}</p> : null}
      {action ? <div className="mt-1">{action}</div> : null}
    </div>
  );
}
