import { Check, LoaderCircle, WifiOff } from 'lucide-react';
import { useEffect, useState } from 'react';
import { cn } from '../../lib/cn';
import { Button } from './Button';

export type BannerState = 'offline' | 'reconnecting' | 'reconnected' | null;

export interface OfflineBannerProps {
  state: BannerState;
  /** Thời điểm (ms epoch) của lần tự thử kế tiếp, để đếm ngược "thử lại sau 8 giây". */
  nextRetryAt?: number | null;
  onRetryNow?: () => void;
  className?: string;
}

function useSecondsUntil(at: number | null | undefined): number | null {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!at) return;
    const t = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(t);
  }, [at]);
  if (!at) return null;
  return Math.max(0, Math.ceil((at - now) / 1000));
}

/**
 * Dải 36px dính trên, z banner (DESIGN §5.5): Mất kết nối (failed) · Đang kết nối lại (running, đếm ngược) ·
 * Đã kết nối lại (ok — nơi gọi tự ẩn sau 3 giây).
 */
/** Ghi chiều cao banner vào `--sr-banner-h` để topbar dính ngay dưới nó (banner có thể xuống dòng trên điện thoại). */
function usePublishHeight(el: HTMLElement | null) {
  useEffect(() => {
    const root = document.documentElement;
    if (!el) {
      root.style.setProperty('--sr-banner-h', '0px');
      return;
    }
    const write = () => root.style.setProperty('--sr-banner-h', `${el.offsetHeight}px`);
    write();
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(write) : null;
    ro?.observe(el);
    return () => {
      ro?.disconnect();
      root.style.setProperty('--sr-banner-h', '0px');
    };
  }, [el]);
}

export function OfflineBanner({ state, nextRetryAt, onRetryNow, className }: OfflineBannerProps) {
  const secs = useSecondsUntil(state === 'reconnecting' ? nextRetryAt : null);
  const [el, setEl] = useState<HTMLDivElement | null>(null);
  usePublishHeight(state ? el : null);
  if (!state) return null;

  return (
    <div
      ref={setEl}
      role="status"
      aria-live="polite"
      className={cn(
        'sticky top-0 z-banner flex min-h-9 flex-wrap items-center gap-x-3 gap-y-1 px-3 py-1 text-sm',
        state === 'offline' && 'sr-banner-offline',
        state === 'reconnecting' && 'sr-banner-reconnecting',
        state === 'reconnected' && 'sr-banner-ok',
        className,
      )}
    >
      {state === 'offline' ? (
        <>
          <WifiOff className="sr-ico text-state-failed" aria-hidden />
          <span className="min-w-0 flex-1">Máy nhà không phản hồi. Việc đang chạy vẫn tiếp tục trên máy.</span>
          {onRetryNow ? (
            <Button className="sr-hit" onClick={onRetryNow}>
              Thử ngay
            </Button>
          ) : null}
        </>
      ) : null}
      {state === 'reconnecting' ? (
        <>
          <LoaderCircle className="sr-ico sr-spin text-state-running" aria-hidden />
          <span className="min-w-0 flex-1">
            Đang kết nối lại
            {secs != null ? (
              <>
                {' '}
                · thử lại sau <span className="tnum">{secs}</span> giây
              </>
            ) : null}
          </span>
          {onRetryNow ? (
            <Button variant="ghost" className="sr-hit" onClick={onRetryNow}>
              Thử ngay
            </Button>
          ) : null}
        </>
      ) : null}
      {state === 'reconnected' ? (
        <>
          <Check className="sr-ico text-ok" aria-hidden />
          <span>Đã kết nối lại</span>
        </>
      ) : null}
    </div>
  );
}
