import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState, type ReactNode } from 'react';
import { toApiError } from '../../api/client';
import { qk } from '../../api/hooks';
import { callOp } from '../../api/ops';
import type { components } from '../../api/schema';
import { Checkbox } from '../../components/ui/Checkbox';
import { Input } from '../../components/ui/Input';
import { SourceBadge } from '../../components/ui/SourceBadge';
import { Textarea } from '../../components/ui/Textarea';
import { toast } from '../../stores/toast';

export type Resolved = components['schemas']['Resolved'];
export type SettingDef = components['schemas']['SettingDef'];

export function useGlobalSettings() {
  const explain = useQuery({
    queryKey: qk.settings,
    queryFn: async () => (await callOp('settings.explain', {})) as Resolved[],
  });
  const schema = useQuery({
    queryKey: qk.settingsSchema,
    queryFn: async () => (await callOp('settings.schema', {})) as SettingDef[],
    staleTime: 5 * 60_000,
  });
  return { explain, schema };
}

/**
 * Ghi tầng global (`settings.set_global`; `null` = bỏ ghi đè, về mặc định). Thành công → toast có Hoàn tác (ghi lại
 * giá trị ghi đè trước đó).
 */
export function useSetGlobal() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (v: { key: string; value: unknown; previous: unknown; label: string; silent?: boolean }) =>
      (await callOp('settings.set_global', { values: { [v.key]: v.value } })) as Resolved[],
    onSuccess: (data, v) => {
      qc.setQueryData(qk.settings, data);
      void qc.invalidateQueries({ queryKey: qk.budget });
      if (v.silent) return;
      toast({
        tone: 'success',
        message: v.value === null ? `Đã đưa “${v.label}” về mặc định.` : `Đã lưu “${v.label}”.`,
        undo: async () => {
          const back = (await callOp('settings.set_global', { values: { [v.key]: v.previous } })) as Resolved[];
          qc.setQueryData(qk.settings, back);
          void qc.invalidateQueries({ queryKey: qk.budget });
        },
      });
    },
  });
}

const display = (v: unknown): string => {
  if (v === true) return 'Bật';
  if (v === false) return 'Tắt';
  if (Array.isArray(v)) return v.length ? v.join(', ') : '(trống)';
  if (v === '' || v == null) return '(trống)';
  return String(v);
};

export interface GlobalSettingProps {
  settingKey: string;
  label: string;
  hint?: ReactNode;
  resolved: Resolved[] | undefined;
  schema: SettingDef[] | undefined;
  /** `lines`: danh sách chuỗi JSON, mỗi dòng một mục (vd host công khai). */
  control?: 'auto' | 'lines';
  /** Kiểm tra trước khi lưu; trả câu lỗi tiếng Việt hoặc null. */
  validate?: (v: unknown) => string | null;
  /** Câu phụ thêm sau hint (vd "0 = không giới hạn"). */
  suffix?: ReactNode;
  disabled?: boolean;
}

/**
 * Một khoá cài đặt toàn cục: control theo kiểu (bool → ô đánh dấu, lưu ngay; số/chuỗi → lưu khi rời ô hoặc Enter;
 * danh sách → mỗi dòng một mục) + SourceBadge (mặc định / đè ở Global, bấm để về mặc định). Tự lưu, có Hoàn tác.
 */
export function GlobalSetting({ settingKey, label, hint, resolved, schema, control = 'auto', validate, suffix, disabled }: GlobalSettingProps) {
  const r = Array.isArray(resolved) ? resolved.find((x) => x.key === settingKey) : undefined;
  const def = Array.isArray(schema) ? schema.find((x) => x.key === settingKey) : undefined;
  const set = useSetGlobal();
  const [draft, setDraft] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const id = `set-${settingKey.replace(/\W/g, '-')}`;

  const asText = (v: unknown) => (control === 'lines' && Array.isArray(v) ? v.join('\n') : v == null ? '' : String(v));
  useEffect(() => {
    if (r) setDraft(asText(r.value));
    // đồng bộ khi giá trị server đổi (hoàn tác, tải lại)
  }, [r?.value]);

  if (!r || !def) return null;
  const previous = r.source === 'global' ? r.value : null;
  const badge = (
    <SourceBadge
      source={r.source === 'global' ? 'global' : 'default'}
      editing="global"
      values={r.source === 'global' ? { global: display(r.value) } : {}}
      defaultValue={display(def.default)}
      onClearOverride={() => set.mutate({ key: settingKey, value: null, previous, label })}
    />
  );

  const save = (value: unknown) => {
    const msg = validate?.(value) ?? null;
    setError(msg);
    if (msg) return;
    if (JSON.stringify(value) === JSON.stringify(r.value)) return;
    setSaved(false);
    set.mutate(
      { key: settingKey, value, previous, label },
      {
        onSuccess: () => setSaved(true),
        onError: (e) => {
          const err = toApiError(e);
          setError(`${err.detail}${err.hint ? ` ${err.hint}` : ''}`);
        },
      },
    );
  };

  const parse = (): { ok: true; value: unknown } | { ok: false; error: string } => {
    const t = draft.trim();
    if (control === 'lines') return { ok: true, value: draft.split(/\r?\n/).map((s) => s.trim()).filter(Boolean) };
    if (def.type === 'int') {
      if (!/^-?\d+$/.test(t)) return { ok: false, error: `${label} phải là số nguyên.` };
      return { ok: true, value: Number(t) };
    }
    if (def.type === 'float') {
      const n = Number(t.replace(',', '.'));
      if (!t || !Number.isFinite(n)) return { ok: false, error: `${label} phải là số.` };
      return { ok: true, value: n };
    }
    return { ok: true, value: t };
  };

  const commit = () => {
    const p = parse();
    if ('error' in p) {
      setError(p.error);
      return;
    }
    save(p.value);
  };

  const hintNode = hint || suffix ? (
    <>
      {hint}
      {hint && suffix ? ' ' : null}
      {suffix}
    </>
  ) : undefined;

  if (def.type === 'bool') {
    return (
      <div className="grid gap-1">
        <div className="flex flex-wrap items-center gap-x-2">
          <Checkbox
            label={label}
            checked={r.value === true}
            disabled={disabled || set.isPending}
            onChange={(e) => save(e.target.checked)}
          />
          {badge}
        </div>
        {hintNode ? <p className="text-xs text-fg-3">{hintNode}</p> : null}
        {error ? (
          <p role="alert" className="text-xs text-state-failed">
            {error}
          </p>
        ) : null}
      </div>
    );
  }

  if (control === 'lines') {
    return (
      <Textarea
        id={id}
        label={label}
        labelExtra={badge}
        hint={hintNode}
        error={error}
        value={draft}
        disabled={disabled}
        minRows={2}
        maxRows={6}
        spellCheck={false}
        className="font-mono text-sm md:text-sm"
        onChange={(e) => {
          setDraft(e.target.value);
          setSaved(false);
        }}
        onBlur={commit}
      />
    );
  }

  return (
    <Input
      id={id}
      label={label}
      labelExtra={badge}
      hint={hintNode}
      error={error}
      success={saved && !error}
      value={draft}
      disabled={disabled}
      inputMode={def.type === 'int' ? 'numeric' : undefined}
      spellCheck={false}
      className={def.type === 'int' || def.type === 'float' ? 'font-mono tnum' : undefined}
      onChange={(e) => {
        setDraft(e.target.value);
        setSaved(false);
      }}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          commit();
        }
      }}
    />
  );
}
