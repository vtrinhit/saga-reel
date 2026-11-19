import { Plus, Save, X } from 'lucide-react';
import { useState, type KeyboardEvent } from 'react';
import { callOp } from '../../api/ops';
import { parseEpisode, rangeError, type Look } from '../../lib/library';
import { Button } from '../ui/Button';
import { controlClass } from '../ui/Field';
import { Input } from '../ui/Input';
import { Textarea } from '../ui/Textarea';
import { useLibMutation } from './useLibrary';

/** Danh sách "phải giữ" dạng chip: Enter/dấu phẩy để thêm, × để bỏ. */
export function FeatureChips({
  id,
  value,
  onChange,
  disabled,
  label = 'Phải giữ',
  hint = 'Nét không bao giờ được đổi giữa các cảnh (khuôn mặt, tóc, vết sẹo…). Enter để thêm.',
  placeholder = 'vd. dây đỏ trên tóc',
}: {
  id: string;
  value: string[];
  onChange: (v: string[]) => void;
  disabled?: boolean;
  label?: string;
  hint?: string;
  placeholder?: string;
}) {
  const [draft, setDraft] = useState('');
  const add = () => {
    const t = draft.trim();
    if (t && !value.includes(t)) onChange([...value, t]);
    setDraft('');
  };
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault();
      add();
    } else if (e.key === 'Backspace' && !draft && value.length) {
      onChange(value.slice(0, -1));
    }
  };
  return (
    <div className="grid gap-1.5">
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      {value.length ? (
        <ul className="flex flex-wrap gap-1" aria-label={label}>
          {value.map((f) => (
            <li key={f} className="inline-flex max-w-full items-center gap-0.5 rounded-xs bg-bg-3 pl-1.5 text-xs">
              <span className="truncate">{f}</span>
              <button
                type="button"
                disabled={disabled}
                onClick={() => onChange(value.filter((x) => x !== f))}
                className="sr-hit grid h-5 w-5 place-items-center rounded-xs text-fg-2 hover:text-fg-1"
                aria-label={`Bỏ "${f}"`}
              >
                <X className="h-3 w-3" strokeWidth={2} aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      <div className="flex gap-1.5">
        <input
          id={id}
          value={draft}
          disabled={disabled}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKey}
          placeholder={placeholder}
          aria-describedby={`${id}-hint`}
          className={controlClass}
        />
        <Button icon={Plus} onClick={add} disabled={disabled || !draft.trim()}>
          Thêm
        </Button>
      </div>
      <p id={`${id}-hint`} className="text-xs text-fg-3">
        {hint}
      </p>
    </div>
  );
}

interface LookDraft {
  name: string;
  lock_word: string;
  description_delta: string;
  keep_features: string[];
  valid_from: string;
  valid_to: string;
}

const toDraft = (l: Look): LookDraft => ({
  name: l.name,
  lock_word: l.lock_word ?? '',
  description_delta: l.description_delta ?? '',
  keep_features: [...(l.keep_features ?? [])].map(String),
  valid_from: l.valid_from == null ? '' : String(l.valid_from),
  valid_to: l.valid_to == null ? '' : String(l.valid_to),
});

/**
 * Phần nhận diện của look: cụm khoá (lock word, chèn nguyên văn), khác biệt so với mô tả gốc, nét phải giữ, khoảng tập
 * dùng look. Lưu một lần bằng `look.update` (kèm `expected_updated_at` chống ghi đè).
 */
export function LookIdentityForm({ look, readOnly }: { look: Look; readOnly?: boolean }) {
  const [base, setBase] = useState(() => ({ key: `${look.id}:${look.updated_at}`, draft: toDraft(look) }));
  const key = `${look.id}:${look.updated_at}`;
  // Look khác hoặc server có bản mới: nạp lại (giữ nguyên nếu đang sửa dở cùng look → so theo key).
  const [draft, setDraft] = useState<LookDraft>(base.draft);
  if (base.key !== key) {
    const fresh = toDraft(look);
    setBase({ key, draft: fresh });
    setDraft(fresh);
  }
  const from = parseEpisode(draft.valid_from);
  const to = parseEpisode(draft.valid_to);
  const rErr = rangeError(from, to);
  const dirty = JSON.stringify(draft) !== JSON.stringify(base.draft);
  const save = useLibMutation(
    () =>
      callOp('look.update', {
        id: look.id,
        values: {
          name: draft.name.trim() || look.name,
          lock_word: draft.lock_word.trim(),
          description_delta: draft.description_delta,
          keep_features: draft.keep_features,
          valid_from: from,
          valid_to: to,
        },
        expected_updated_at: look.updated_at,
      }),
    { fail: 'Không lưu được look.', success: () => `Đã lưu look ${draft.name.trim() || look.name}.` },
  );
  const set = <K extends keyof LookDraft>(k: K, v: LookDraft[K]) => setDraft((d) => ({ ...d, [k]: v }));
  const fid = (s: string) => `look-${look.id}-${s}`;

  return (
    <form
      className="grid gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (!rErr && dirty) save.mutate(undefined);
      }}
    >
      <Input
        id={fid('lock')}
        label="Cụm khoá (lock word)"
        value={draft.lock_word}
        disabled={readOnly}
        onChange={(e) => set('lock_word', e.target.value)}
        placeholder="màu + chất liệu + vật, vd. crimson silk áo dài with gold fox embroidery"
        hint="Chèn nguyên văn vào mọi prompt đồng bộ dùng look này."
        className="font-mono text-sm md:text-sm"
      />
      <Textarea
        id={fid('delta')}
        label="Khác với mô tả gốc"
        value={draft.description_delta}
        disabled={readOnly}
        onChange={(e) => set('description_delta', e.target.value)}
        minRows={2}
        maxRows={6}
        hint="Chỉ ghi phần khác (trang phục, tuổi, biểu cảm) — mô tả khoá của tài sản vẫn được ghép trước."
      />
      <FeatureChips id={fid('keep')} value={draft.keep_features} onChange={(v) => set('keep_features', v)} disabled={readOnly} />
      <fieldset className="grid gap-1.5">
        <legend className="mb-1.5 text-sm font-medium">Dùng cho tập</legend>
        <div className="grid grid-cols-2 gap-2">
          <Input
            id={fid('from')}
            label={<span className="text-xs font-normal text-fg-2">Từ tập</span>}
            inputMode="numeric"
            value={draft.valid_from}
            disabled={readOnly}
            onChange={(e) => set('valid_from', e.target.value)}
            placeholder="đầu"
            aria-invalid={rErr ? true : undefined}
          />
          <Input
            id={fid('to')}
            label={<span className="text-xs font-normal text-fg-2">Tới tập</span>}
            inputMode="numeric"
            value={draft.valid_to}
            disabled={readOnly}
            onChange={(e) => set('valid_to', e.target.value)}
            placeholder="cuối"
            aria-invalid={rErr ? true : undefined}
          />
        </div>
        {rErr ? (
          <p role="alert" className="text-xs text-state-failed">
            {rErr}
          </p>
        ) : (
          <p className="text-xs text-fg-3">Để trống = mọi tập. Clip tự chọn look theo số tập.</p>
        )}
      </fieldset>
      {!readOnly ? (
        <div className="flex items-center justify-end gap-2">
          {dirty ? (
            <Button variant="ghost" onClick={() => setDraft(base.draft)} disabled={save.isPending}>
              Bỏ thay đổi
            </Button>
          ) : null}
          <Button type="submit" icon={Save} disabled={!dirty || !!rErr} loading={save.isPending}>
            Lưu look
          </Button>
        </div>
      ) : null}
    </form>
  );
}
