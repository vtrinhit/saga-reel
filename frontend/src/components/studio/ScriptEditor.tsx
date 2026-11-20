/**
 * Kịch bản của tập trong Studio (DESIGN §4.3): một cột tối đa 72 ký tự/dòng, lưu khi rời ô (`sequence.update`
 * với `expected_updated_at`). Viết bằng AI / chữ chạy dần là màn Kịch bản của Task M.
 */
import { useEffect, useId, useState } from 'react';
import { cn, focusRing } from '../../lib/cn';

export function ScriptEditor({ value, readOnly, saving, onSave }: {
  value: string;
  readOnly?: boolean;
  saving?: boolean;
  onSave: (text: string) => void;
}) {
  const id = useId();
  const [text, setText] = useState(value);
  const [dirty, setDirty] = useState(false);
  useEffect(() => {
    if (!dirty) setText(value);
  }, [value, dirty]);
  const words = text.trim() ? text.trim().split(/\s+/).length : 0;
  return (
    <div className="mx-auto grid w-full max-w-[76ch] gap-2">
      <div className="flex items-baseline justify-between gap-3">
        <label htmlFor={id} className="text-sm font-semibold">
          Kịch bản của tập
        </label>
        <span className="font-mono text-xs tabular-nums text-fg-3" aria-live="polite">
          {saving ? 'Đang lưu…' : dirty ? 'Chưa lưu' : `${words} từ`}
        </span>
      </div>
      <textarea
        id={id}
        value={text}
        readOnly={readOnly}
        spellCheck={false}
        placeholder="Tập này chưa có kịch bản. Viết hoặc dán kịch bản ở đây; chữ được lưu khi bạn rời ô."
        onChange={(e) => {
          setText(e.target.value);
          setDirty(true);
        }}
        onBlur={() => {
          if (dirty && text !== value) onSave(text);
          setDirty(false);
        }}
        className={cn(
          'min-h-[40dvh] w-full md:min-h-[60vh] resize-y rounded-sm border border-line-3 bg-bg-2 p-4 font-mono text-sm leading-relaxed text-fg-1 md:text-base',
          'max-w-[72ch] placeholder:text-fg-3',
          focusRing,
        )}
      />
    </div>
  );
}
