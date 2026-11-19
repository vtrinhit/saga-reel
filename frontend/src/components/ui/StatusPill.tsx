import { Check, CircleCheck, CircleAlert, CircleDot, Clock, Diamond, History, LoaderCircle, ScanFace, AlertTriangle, type LucideIcon } from 'lucide-react';
import { cn } from '../../lib/cn';

/**
 * Bảy trạng thái clip (DESIGN §3.2) + hai tông hệ thống (warn, ok) cho provider/ngân sách + `wait` (v2 §5.6: viền đứt
 * accent, nền trong suốt — "chờ bạn").
 */
export type ClipStatus = 'queued' | 'running' | 'done' | 'failed' | 'stale' | 'drifted' | 'approved';
export type PillStatus = ClipStatus | 'warn' | 'ok' | 'wait';

export const STATUS_ICON: Record<PillStatus, LucideIcon> = {
  queued: Clock,
  running: LoaderCircle,
  done: Check,
  failed: AlertTriangle,
  stale: History,
  drifted: ScanFace,
  approved: CircleCheck,
  warn: CircleAlert,
  ok: CircleDot,
  wait: Diamond,
};

export const CLIP_STATUSES: ClipStatus[] = ['queued', 'running', 'done', 'failed', 'stale', 'drifted', 'approved'];

/** Nhãn cố định: Chờ #n · Đang tạo n% · Xong · Lỗi · Đã cũ · Lệch nhất quán · Đã chọn. */
export function statusLabel(status: PillStatus, opts: { position?: number | null; progress?: number | null } = {}): string {
  switch (status) {
    case 'queued':
      return opts.position != null ? `Chờ #${opts.position}` : 'Chờ';
    case 'running':
      return opts.progress != null ? `Đang tạo ${Math.round(opts.progress)}%` : 'Đang tạo';
    case 'done':
      return 'Xong';
    case 'failed':
      return 'Lỗi';
    case 'stale':
      return 'Đã cũ';
    case 'drifted':
      return 'Lệch nhất quán';
    case 'approved':
      return 'Đã chọn';
    case 'warn':
      return 'Cảnh báo';
    case 'ok':
      return 'Ổn';
    case 'wait':
      return 'Chờ bạn';
  }
}

export interface StatusPillProps {
  status: PillStatus;
  /** Vị trí trong hàng đợi chung (queued). */
  position?: number | null;
  /** 0–100 (running). */
  progress?: number | null;
  /** Ghi đè nhãn (vd "MaiVideo · ổn"); icon + màu vẫn theo trạng thái. */
  label?: string;
  /** Đặt trên ảnh/footage: nền bg-0 85% để đọc được trên mọi màu. */
  onMedia?: boolean;
  title?: string;
  className?: string;
}

/** Cao 20px, padding 0/8, r-sm, icon 12 + chữ xs 500; nền màu trạng thái 16% trộn bg-2, chữ màu trạng thái. */
export function StatusPill({ status, position, progress, label, onMedia, title, className }: StatusPillProps) {
  const Icon = STATUS_ICON[status];
  return (
    <span
      data-status={status}
      title={title}
      className={cn(
        'sr-pill inline-flex h-5 max-w-full items-center gap-1 whitespace-nowrap rounded-sm px-2 text-xs font-medium tnum',
        'transition-colors duration-base ease-out',
        onMedia && 'sr-pill-on-media',
        className,
      )}
    >
      <Icon className={cn('h-3 w-3 flex-none', status === 'running' && 'sr-spin')} strokeWidth={1.75} aria-hidden />
      <span className="truncate">{label ?? statusLabel(status, { position, progress })}</span>
    </span>
  );
}
