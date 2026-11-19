import { forwardRef, type InputHTMLAttributes, type ReactNode } from 'react';
import { cn, focusRing } from '../../lib/cn';

export interface CheckboxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> {
  label: ReactNode;
}

/** Ô đánh dấu có nhãn; cả dòng là vùng chạm ≥ 44px. */
export const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(function Checkbox(
  { label, className, ...rest },
  ref,
) {
  return (
    <label className={cn('inline-flex min-h-touch cursor-pointer items-center gap-2.5 text-sm', className)}>
      <input
        ref={ref}
        type="checkbox"
        className={cn('m-0 h-[18px] w-[18px] flex-none rounded-sm accent-accent', focusRing)}
        {...rest}
      />
      <span>{label}</span>
    </label>
  );
});
