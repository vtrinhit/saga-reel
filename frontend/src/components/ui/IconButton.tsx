import type { LucideIcon } from 'lucide-react';
import { forwardRef, type ButtonHTMLAttributes } from 'react';
import { cn, focusRing } from '../../lib/cn';

export interface IconButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children'> {
  icon: LucideIcon;
  /** Bắt buộc: thành `aria-label` và tooltip (trễ 600ms khi hover, hiện ngay khi focus). */
  label: string;
  /** Nút bật/tắt: icon accent + nền bg-3, kèm `aria-pressed`. */
  pressed?: boolean;
  /** Tắt tooltip (vd khi nút đã có nhãn chữ bên cạnh). */
  noTip?: boolean;
  /** Vị trí tooltip; mặc định dưới nút. */
  tipSide?: 'bottom' | 'top';
}

/** Vuông `--h-control` (cảm ứng: 44px), icon 16 (cảm ứng: 20). */
export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { icon: Icon, label, pressed, noTip, tipSide = 'bottom', className, type = 'button', ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      aria-label={label}
      aria-pressed={pressed}
      data-tip={noTip ? undefined : label}
      className={cn(
        'inline-grid h-control w-[var(--h-control)] flex-none place-items-center rounded-md border border-transparent text-fg-2',
        'transition-colors duration-fast ease-out hover:bg-bg-3 hover:text-fg-1 active:bg-bg-4',
        'disabled:cursor-not-allowed disabled:bg-transparent disabled:text-fg-4',
        pressed && 'bg-bg-3 text-accent hover:text-accent',
        !noTip && 'sr-tip',
        tipSide === 'top' && '[&::after]:bottom-[calc(100%+6px)] [&::after]:top-auto',
        focusRing,
        className,
      )}
      {...rest}
    >
      <Icon className="sr-ico" aria-hidden />
    </button>
  );
});
