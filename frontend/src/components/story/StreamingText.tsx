import { Square } from 'lucide-react';
import { useEffect, useRef } from 'react';
import { cn } from '../../lib/cn';
import { lastSentence } from '../../lib/story';
import { ActorTag } from '../ui/ActorTag';
import { Button } from '../ui/Button';

export interface StreamingTextProps {
  text: string;
  /** Đang chạy: con trỏ khối accent nhấp nháy + nút Dừng. */
  running: boolean;
  /** Người/agent đang viết (`agent:biên-tập`, `user:owner`). */
  actor?: string | null;
  /** "Viết kịch bản · Tập 14" */
  label?: string;
  onStop?: () => void;
  stopping?: boolean;
  /** Chiều cao tối thiểu giữ chỗ để chữ đổ vào không đẩy layout. */
  minHeight?: string;
  placeholder?: string;
  className?: string;
}

/**
 * Chữ LLM đổ vào theo cụm (DESIGN §5.3): đoạn văn tối đa 68ch, con trỏ khối accent 2px nhấp nháy 1s (đứng yên khi
 * giảm chuyển động), `aria-live="polite"` chỉ đọc câu trọn vẹn cuối cùng (không đọc từng cụm), nút Dừng.
 */
export function StreamingText({
  text,
  running,
  actor,
  label,
  onStop,
  stopping,
  minHeight = '12rem',
  placeholder = 'Chữ sẽ hiện dần ở đây khi bắt đầu viết.',
  className,
}: StreamingTextProps) {
  const box = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  useEffect(() => {
    const el = box.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [text]);
  const said = running ? lastSentence(text) : '';

  return (
    <div className={cn('grid min-w-0 gap-2', className)}>
      {label || actor ? (
        <div className="flex min-w-0 flex-wrap items-center justify-between gap-2 text-xs text-fg-2">
          <span className="truncate py-px">{label}</span>
          {actor ? (
            <span className="inline-flex items-center gap-1.5">
              <ActorTag actor={actor} />
              {running ? <span className="text-state-running">đang viết</span> : null}
            </span>
          ) : null}
        </div>
      ) : null}
      <div
        ref={box}
        onScroll={(e) => {
          const el = e.currentTarget;
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
        }}
        className="relative max-h-[60dvh] overflow-y-auto rounded-md bg-bg-2 px-3 py-2.5"
        style={{ minHeight }}
        tabIndex={0}
        role="region"
        aria-label={label ? `Chữ đang viết: ${label}` : 'Chữ đang viết'}
        aria-busy={running || undefined}
      >
        {text ? (
          <p className="max-w-[68ch] whitespace-pre-wrap break-words text-md leading-[1.65] text-fg-1">
            {text}
            {running ? (
              <span
                aria-hidden
                className="ml-0.5 inline-block h-[1.1em] w-[2px] translate-y-[3px] bg-accent motion-safe:animate-[sr-caret_1s_steps(1)_infinite]"
              />
            ) : null}
          </p>
        ) : (
          <p className="text-sm text-fg-3">{running ? 'Đang chờ chữ đầu tiên…' : placeholder}</p>
        )}
        <style>{'@keyframes sr-caret{50%{opacity:0}}'}</style>
      </div>
      <p className="sr-only" aria-live="polite">
        {said}
      </p>
      {running && onStop ? (
        <Button icon={Square} onClick={onStop} loading={stopping} className="justify-self-start">
          Dừng
        </Button>
      ) : null}
    </div>
  );
}
