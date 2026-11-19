import { AlertTriangle, RotateCw, ScrollText } from 'lucide-react';
import { toApiError } from '../../api/client';
import { cn } from '../../lib/cn';
import { Button } from './Button';

export interface ErrorStateProps {
  /** Cái gì hỏng, dạng "Không tải được danh sách tập". */
  what: string;
  /** Lỗi gốc (ApiError hoặc bất kỳ) — chỉ lấy `detail`/`hint`, không bao giờ hiện `extra`. */
  error?: unknown;
  onRetry?: () => void;
  retrying?: boolean;
  onViewLog?: () => void;
  className?: string;
}

/** Cái gì hỏng + cách sửa + nút. Không xin lỗi, không "Đã có lỗi xảy ra" (DESIGN §5.5). */
export function ErrorState({ what, error, onRetry, retrying, onViewLog, className }: ErrorStateProps) {
  const e = error != null ? toApiError(error) : null;
  const cause = e ? (e.status > 0 ? `máy chủ trả lỗi ${e.status}: ${e.detail}` : e.detail) : null;
  return (
    <div role="alert" className={cn('grid justify-items-start gap-2', className)}>
      <AlertTriangle className="h-8 w-8 text-state-failed" strokeWidth={1.75} aria-hidden />
      <p className="text-md font-medium">
        {what}
        {cause ? <span className="font-normal text-fg-2"> ({cause.replace(/\.$/, '')}).</span> : '.'}
      </p>
      {e?.hint ? <p className="max-w-prose text-sm text-fg-2">{e.hint}</p> : null}
      {onRetry || onViewLog ? (
        <div className="mt-1 flex flex-wrap gap-2">
          {onRetry ? (
            <Button icon={RotateCw} onClick={onRetry} loading={retrying}>
              Thử lại
            </Button>
          ) : null}
          {onViewLog ? (
            <Button variant="ghost" icon={ScrollText} onClick={onViewLog}>
              Xem log
            </Button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
