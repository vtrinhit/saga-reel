import { forwardRef, useCallback, useLayoutEffect, useRef, type ReactNode, type TextareaHTMLAttributes } from 'react';
import { cn } from '../../lib/cn';
import { formatCount } from '../../lib/format';
import { controlClass, describedBy, Field } from './Field';

export interface TextareaProps extends Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, 'id' | 'value'> {
  id: string;
  label: ReactNode;
  labelExtra?: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  value: string;
  /** Tự giãn giữa minRows và maxRows (mặc định 3–12). */
  minRows?: number;
  maxRows?: number;
  /** Đếm ký tự ở góc dưới phải (mono xs). */
  showCount?: boolean;
  fieldClassName?: string;
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { id, label, labelExtra, hint, error, value, minRows = 3, maxRows = 12, showCount, maxLength, className, fieldClassName, ...rest },
  ref,
) {
  const inner = useRef<HTMLTextAreaElement | null>(null);
  const setRefs = useCallback(
    (el: HTMLTextAreaElement | null) => {
      inner.current = el;
      if (typeof ref === 'function') ref(el);
      else if (ref) ref.current = el;
    },
    [ref],
  );

  useLayoutEffect(() => {
    const el = inner.current;
    if (!el) return;
    const style = getComputedStyle(el);
    const line = parseFloat(style.lineHeight) || 22;
    const pad = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom) + 2;
    el.style.height = 'auto';
    const h = Math.min(Math.max(el.scrollHeight, minRows * line + pad), maxRows * line + pad);
    el.style.height = `${h}px`;
    el.style.overflowY = el.scrollHeight > h ? 'auto' : 'hidden';
  }, [value, minRows, maxRows]);

  return (
    <Field id={id} label={label} labelExtra={labelExtra} hint={hint} error={error} className={fieldClassName}>
      <div className="relative min-w-0">
        <textarea
          ref={setRefs}
          id={id}
          value={value}
          maxLength={maxLength}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy(id, hint, error)}
          className={cn(controlClass, 'resize-none py-2 text-md', showCount && 'pb-6', className)}
          {...rest}
        />
        {showCount ? (
          <span className="pointer-events-none absolute bottom-1.5 right-2.5 font-mono text-xs text-fg-3 tnum" aria-hidden>
            {formatCount(value.length)}
            {maxLength ? `/${formatCount(maxLength)}` : ''}
          </span>
        ) : null}
      </div>
    </Field>
  );
});
