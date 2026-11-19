import { CircleCheck } from 'lucide-react';
import { forwardRef, type InputHTMLAttributes, type ReactNode } from 'react';
import { cn } from '../../lib/cn';
import { controlClass, describedBy, Field } from './Field';

export interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'id'> {
  id: string;
  label: ReactNode;
  labelExtra?: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  /** Đã kiểm tra/lưu thành công: dấu ok ở cuối ô. */
  success?: boolean;
  /** Phần tử đè bên phải trong ô (vd nút hiện/ẩn mật khẩu). */
  trailing?: ReactNode;
  fieldClassName?: string;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { id, label, labelExtra, hint, error, success, trailing, className, fieldClassName, ...rest },
  ref,
) {
  return (
    <Field id={id} label={label} labelExtra={labelExtra} hint={hint} error={error} className={fieldClassName}>
      <div className="relative min-w-0">
        <input
          ref={ref}
          id={id}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy(id, hint, error)}
          className={cn(controlClass, (trailing || success) && 'pr-11', className)}
          {...rest}
        />
        {trailing ? <div className="absolute inset-y-0 right-0 flex items-center">{trailing}</div> : null}
        {!trailing && success ? (
          <CircleCheck
            className="sr-ico pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-ok"
            aria-label="Hợp lệ"
          />
        ) : null}
      </div>
    </Field>
  );
});
