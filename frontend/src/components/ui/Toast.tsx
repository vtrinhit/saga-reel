import { AlertTriangle, Check, Undo2, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { cn } from '../../lib/cn';
import { useToasts, type ToastItem } from '../../stores/toast';
import { Button } from './Button';
import { IconButton } from './IconButton';

function ToastView({ t, onDismiss }: { t: ToastItem; onDismiss: () => void }) {
  const [paused, setPaused] = useState(false);
  const remaining = useRef(t.duration);
  const startedAt = useRef(0);

  // Đếm lùi, dừng khi hover/focus (DESIGN §5.5).
  useEffect(() => {
    if (paused) return;
    startedAt.current = Date.now();
    const timer = setTimeout(onDismiss, remaining.current);
    return () => {
      clearTimeout(timer);
      remaining.current -= Date.now() - startedAt.current;
    };
  }, [paused, onDismiss]);

  const Icon = t.tone === 'error' ? AlertTriangle : t.tone === 'success' ? Check : null;

  return (
    <div
      onPointerEnter={() => setPaused(true)}
      onPointerLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setPaused(false);
      }}
      className="sr-enter-rise pointer-events-auto flex w-full min-w-0 items-center gap-3 rounded-md border border-line-2 bg-bg-4 py-2 pl-3 pr-2 text-sm shadow-pop"
    >
      {Icon ? (
        <Icon className={cn('sr-ico', t.tone === 'error' ? 'text-state-failed' : 'text-ok')} aria-hidden />
      ) : null}
      <p className="min-w-0 flex-1">{t.message}</p>
      {t.undo ? (
        <Button
          variant="ghost"
          icon={Undo2}
          onClick={() => {
            t.undo?.();
            onDismiss();
          }}
        >
          Hoàn tác
        </Button>
      ) : null}
      {t.action ? (
        <Button
          variant="ghost"
          onClick={() => {
            t.action?.onClick();
            onDismiss();
          }}
        >
          {t.action.label}
        </Button>
      ) : null}
      <IconButton icon={X} label="Đóng thông báo" noTip onClick={onDismiss} />
    </div>
  );
}

function isTyping(el: Element | null): boolean {
  if (!(el instanceof HTMLElement)) return false;
  return el.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName);
}

/**
 * Vùng toast: dưới phải (điện thoại: trên thanh dưới — dùng biến `--sr-bottom-offset` do shell đặt), tối đa 3,
 * `aria-live=polite`, không cướp focus. `Ctrl Z` hoàn tác toast mới nhất có Hoàn tác (khi không đang gõ).
 */
export function ToastViewport() {
  const toasts = useToasts((s) => s.toasts);
  const dismiss = useToasts((s) => s.dismiss);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || e.shiftKey || e.key.toLowerCase() !== 'z' || isTyping(document.activeElement)) return;
      const latest = [...useToasts.getState().toasts].reverse().find((t) => t.undo);
      if (!latest) return;
      e.preventDefault();
      latest.undo?.();
      useToasts.getState().dismiss(latest.id);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  return (
    <div
      role="status"
      aria-live="polite"
      className="pointer-events-none fixed inset-x-4 bottom-[calc(16px+var(--sr-bottom-offset,0px))] z-toast flex flex-col items-stretch gap-2 md:left-auto md:right-4 md:w-[420px]"
    >
      {toasts.map((t) => (
        <ToastView key={t.id} t={t} onDismiss={() => dismiss(t.id)} />
      ))}
    </div>
  );
}
