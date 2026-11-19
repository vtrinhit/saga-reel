import { LoaderCircle, type LucideIcon } from 'lucide-react';
import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react';
import { cn, focusRing } from '../../lib/cn';

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger';

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  /** `lg` = 48px (nút chính trên điện thoại, màn đăng nhập). */
  size?: 'md' | 'lg';
  icon?: LucideIcon;
  /** Đang chạy: spinner thay icon, giữ nguyên chiều rộng, `aria-busy`. */
  loading?: boolean;
  /** Trạng thái phản hồi sau hành động. */
  tone?: 'error' | 'success';
  /** Giá ghi trên nút trả phí: "Tạo lại · 4 lượt". */
  cost?: ReactNode;
  block?: boolean;
  /** Nút danger đã "lên cò" (bước 1 của xoá hai bước). */
  armed?: boolean;
}

const base =
  'relative inline-flex select-none items-center justify-center gap-2 whitespace-nowrap rounded-md border px-3 text-sm font-medium ' +
  'transition-colors duration-fast ease-out [touch-action:manipulation] ' +
  'disabled:cursor-not-allowed aria-disabled:cursor-not-allowed';

const variants: Record<ButtonVariant, string> = {
  primary:
    'border-accent bg-accent text-accent-on hover:border-accent-hi hover:bg-accent-hi active:border-accent-lo active:bg-accent-lo ' +
    'disabled:border-bg-3 disabled:bg-bg-3 disabled:text-fg-4',
  secondary:
    'border-line-2 bg-bg-2 text-fg-1 hover:border-line-3 hover:bg-bg-3 active:bg-bg-4 ' +
    'disabled:border-line-1 disabled:bg-bg-1 disabled:text-fg-4',
  ghost:
    'border-transparent bg-transparent text-fg-1 hover:bg-bg-3 active:bg-bg-4 disabled:bg-transparent disabled:text-fg-4',
  danger: 'sr-btn-danger disabled:border-line-1 disabled:bg-bg-1 disabled:text-fg-4',
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = 'secondary',
    size = 'md',
    icon: Icon,
    loading = false,
    tone,
    cost,
    block,
    armed,
    className,
    children,
    disabled,
    type = 'button',
    ...rest
  },
  ref,
) {
  const isDisabled = disabled || loading;
  return (
    <button
      ref={ref}
      type={type}
      disabled={isDisabled}
      aria-disabled={isDisabled || undefined}
      aria-busy={loading || undefined}
      data-armed={armed ? 'true' : undefined}
      className={cn(
        base,
        size === 'lg' ? 'h-12 px-4 text-md' : 'h-control',
        variants[variant],
        tone === 'error' && 'border-state-failed text-state-failed',
        tone === 'success' && 'border-ok/60 text-ok',
        // nút loading không xám đi như disabled thường: vẫn là nút đang làm việc
        loading && variant === 'primary' && 'disabled:border-accent disabled:bg-accent disabled:text-accent-on',
        block && 'w-full',
        focusRing,
        className,
      )}
      {...rest}
    >
      {loading ? (
        <LoaderCircle className="sr-ico sr-spin" aria-hidden />
      ) : Icon ? (
        <Icon className="sr-ico" aria-hidden />
      ) : null}
      {children != null && <span className="truncate">{children}</span>}
      {cost != null && <span className="font-mono tnum opacity-80">· {cost}</span>}
    </button>
  );
});
