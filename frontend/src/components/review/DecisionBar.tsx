import { Check, CircleAlert, RotateCcw, SkipForward, X } from 'lucide-react';
import { cn } from '../../lib/cn';
import type { SuggestChange } from '../../lib/episodeTypes';
import { Button } from '../ui/Button';

function Kbd({ k }: { k: string }) {
  return (
    <kbd className="ml-1 hidden rounded-xs border border-current/40 px-1 font-mono text-2xs leading-4 opacity-80 md:inline">{k}</kbd>
  );
}

/**
 * Thanh quyết định (DESIGN §6.7): Tạo lại (R) · Bỏ qua (S) · gợi ý phím · **Chọn take n (A)**. Điện thoại: dính trên
 * thanh dưới, 3 nút 48px.
 */
export function DecisionBar({
  takeNo,
  canApprove,
  busy,
  onRegenerate,
  onSkip,
  onApprove,
  className,
}: {
  takeNo: number | null;
  canApprove: boolean;
  busy?: boolean;
  onRegenerate: () => void;
  onSkip: () => void;
  onApprove: () => void;
  className?: string;
}) {
  return (
    <div
      className={cn(
        'grid min-w-0 grid-cols-3 items-center gap-2 border-t border-line-1 bg-bg-0 pt-3',
        'max-md:sticky max-md:bottom-[calc(var(--h-bottomnav)+env(safe-area-inset-bottom,0px))] max-md:z-sticky max-md:-mx-4 max-md:px-4 max-md:pb-2',
        'md:flex md:flex-wrap',
        className,
      )}
    >
      <Button icon={RotateCcw} onClick={onRegenerate} disabled={busy} className="max-md:h-12 md:mr-auto 2xl:mr-0">
        Tạo lại
        <Kbd k="R" />
      </Button>
      <Button icon={SkipForward} variant="secondary" onClick={onSkip} disabled={busy} className="max-md:h-12 md:order-2">
        Bỏ qua
        <Kbd k="S" />
      </Button>
      <p className="hidden min-w-0 flex-1 truncate text-xs text-fg-3 2xl:block md:order-1">
        1–9 chọn take · ↑↓ đổi take · E sửa prompt · V verdict · C so sánh · Space phát · Ctrl Z hoàn tác
      </p>
      <Button
        variant="primary"
        icon={Check}
        onClick={onApprove}
        disabled={!canApprove || busy}
        className="max-md:h-12 md:order-3"
      >
        <span className="md:hidden">Chọn</span>
        <span className="hidden md:inline">Chọn take {takeNo ?? ''}</span>
        <Kbd k="A" />
      </Button>
    </div>
  );
}

/** Băng gợi ý đổi cách khi `take.verdict` trả `suggest_change` (warn 10%). */
export function SuggestBand({
  suggest,
  onEditPrompt,
  onDismiss,
}: {
  suggest: SuggestChange;
  onEditPrompt: () => void;
  onDismiss: () => void;
}) {
  return (
    <div role="status" className="sr-warnbox flex min-w-0 items-start gap-2 rounded-md border border-warn/40 px-3 py-2 text-sm">
      <CircleAlert className="sr-ico mt-0.5 flex-none text-warn" aria-hidden />
      <div className="min-w-0 flex-1">
        <p className="font-semibold">{suggest.message}</p>
        <p className="text-fg-2">Thử sửa prompt, đổi ảnh tham chiếu hoặc đổi engine thay vì tạo lại tiếp.</p>
        <button type="button" onClick={onEditPrompt} className="mt-1 font-medium underline underline-offset-2 hover:text-accent focus-visible:outline focus-visible:outline-focus focus-visible:outline-accent">
          Sửa prompt (E)
        </button>
      </div>
      <button type="button" aria-label="Ẩn gợi ý" onClick={onDismiss} className="grid h-7 w-7 flex-none place-items-center rounded-sm hover:bg-bg-3 focus-visible:outline focus-visible:outline-focus focus-visible:outline-accent">
        <X className="h-4 w-4" aria-hidden />
      </button>
    </div>
  );
}
