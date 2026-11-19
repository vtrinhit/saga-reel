import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Copy, FileCode2, Plus, RotateCcw, Save, Trash2 } from 'lucide-react';
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { useSearchParams } from 'react-router';
import { useIsViewer } from '../../api/auth';
import { callOp } from '../../api/ops';
import { Button } from '../../components/ui/Button';
import { EmptyState } from '../../components/ui/EmptyState';
import { ErrorState } from '../../components/ui/ErrorState';
import { Input } from '../../components/ui/Input';
import { Modal } from '../../components/ui/Modal';
import { SegmentedControl } from '../../components/ui/SegmentedControl';
import { Select } from '../../components/ui/Select';
import { SkeletonRows } from '../../components/ui/Skeleton';
import { errorMessage } from '../../lib/activity';
import { cn, focusRing } from '../../lib/cn';
import {
  FORM_FIELDS,
  formatField,
  fromYaml,
  getPath,
  kindLabel,
  parseField,
  PRESET_KINDS,
  presetErrors,
  setPath,
  toYaml,
  validPresetKey,
  type FormField,
  type Preset,
} from '../../lib/presets';
import { toast } from '../../stores/toast';
import { PageHeader } from '../PageHeader';

const listKey = (kind: string) => ['presets', 'list', kind] as const;

/**
 * `/presets` — Mẫu: mọi loại preset. Nền tảng / thể loại có form gọn + YAML đầy đủ; loại khác sửa bằng YAML. Cài sẵn:
 * sửa được, có "Khôi phục bản gốc" (`preset.reset_builtin`), không xoá được; bản tự tạo: nhân bản / xoá hai bước.
 */
export function PresetsPage() {
  const [params, setParams] = useSearchParams();
  const kind = (PRESET_KINDS.find((k) => k.value === params.get('kind'))?.value ?? 'platform') as string;
  const selKey = params.get('key');
  const viewer = useIsViewer();
  const [creating, setCreating] = useState(false);
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);
  const list = useQuery({
    queryKey: listKey(kind),
    queryFn: async () => (await callOp('preset.list', { kind: kind as 'genre' })) as unknown as Preset[],
  });
  const items = list.data ?? [];
  const selected = items.find((p) => p.key === selKey) ?? items[0] ?? null;

  const setQ = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) {
      if (v == null) next.delete(k);
      else next.set(k, v);
    }
    setParams(next, { replace: true });
  };
  const onTabKey = (e: KeyboardEvent, i: number) => {
    const d = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
    if (!d) return;
    e.preventDefault();
    tabs.current[(i + d + PRESET_KINDS.length) % PRESET_KINDS.length]?.focus();
  };

  return (
    <div className="mx-auto grid w-full max-w-[1400px] gap-4">
      <PageHeader
        title="Mẫu"
        description="Preset dùng chung cho mọi dự án: nền tảng xuất, thể loại, quy trình, phụ đề, giọng… Sửa ở đây đổi cho mọi dự án dùng preset đó."
        actions={
          viewer ? null : (
            <Button icon={Plus} onClick={() => setCreating(true)}>
              Tạo preset {kindLabel(kind).toLowerCase()}
            </Button>
          )
        }
      />
      <div role="tablist" aria-label="Loại preset" className="relative -mx-1 flex overflow-x-auto border-b border-line-1 px-1 scrollbar-none">
        {PRESET_KINDS.map((k, i) => {
          const on = k.value === kind;
          return (
            <button
              key={k.value}
              ref={(el) => {
                tabs.current[i] = el;
              }}
              id={`preset-tab-${k.value}`}
              type="button"
              role="tab"
              aria-selected={on}
              tabIndex={on ? 0 : -1}
              onKeyDown={(e) => onTabKey(e, i)}
              onClick={() => setQ({ kind: k.value, key: null })}
              className={cn(
                'relative h-touch flex-none whitespace-nowrap px-3 text-sm font-medium md:h-10',
                on ? 'text-fg-1 after:absolute after:inset-x-2 after:bottom-0 after:border-b-2 after:border-accent' : 'text-fg-2 hover:text-fg-1',
                focusRing,
                'focus-visible:-outline-offset-2',
              )}
            >
              {k.label}
            </button>
          );
        })}
      </div>

      <div role="tabpanel" aria-labelledby={`preset-tab-${kind}`} className="grid min-w-0 items-start gap-4 lg:grid-cols-[18rem_minmax(0,1fr)]">
        <nav aria-label={`Preset ${kindLabel(kind).toLowerCase()}`} className="min-w-0 rounded-md border border-line-2 bg-bg-1">
          {list.isLoading ? (
            <div className="p-3">
              <SkeletonRows count={5} />
            </div>
          ) : list.error ? (
            <ErrorState className="p-3" what="Không tải được preset" error={list.error} onRetry={() => void list.refetch()} />
          ) : items.length === 0 ? (
            <p className="p-3 text-sm text-fg-2">Chưa có preset {kindLabel(kind).toLowerCase()} nào.</p>
          ) : (
            <ul className="max-h-[40dvh] overflow-y-auto lg:max-h-[calc(100dvh-16rem)]">
              {items.map((p) => {
                const on = selected?.id === p.id;
                return (
                  <li key={p.id} className="border-t border-line-1 first:border-t-0">
                    <button
                      type="button"
                      aria-current={on ? 'true' : undefined}
                      onClick={() => setQ({ key: p.key })}
                      className={cn('grid w-full gap-0.5 px-3 py-2 text-left hover:bg-bg-2', on && 'bg-bg-2 shadow-[inset_2px_0_0_rgb(var(--c-accent))]', focusRing, 'focus-visible:-outline-offset-2')}
                    >
                      <span className="truncate py-px text-sm font-medium">{p.name}</span>
                      <span className="flex items-center gap-2 text-xs text-fg-2">
                        <span className="truncate font-mono">{p.key}</span>
                        {p.builtin ? <span className="flex-none text-fg-3">{p.modified ? 'cài sẵn · đã sửa' : 'cài sẵn'}</span> : null}
                        {p.project_id != null ? <span className="flex-none text-fg-3">dự án #{p.project_id}</span> : null}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </nav>
        {selected ? (
          <PresetEditor key={`${selected.id}:${selected.updated_at ?? ''}`} preset={selected} viewer={viewer} onKey={(key) => setQ({ key })} />
        ) : !list.isLoading ? (
          <div className="rounded-md border border-line-2 bg-bg-1 p-5">
            <EmptyState icon={FileCode2} title="Chọn một preset để xem và sửa." />
          </div>
        ) : null}
      </div>
      <CreatePresetDialog open={creating} kind={kind} onClose={() => setCreating(false)} onCreated={(p) => setQ({ key: p.key })} />
    </div>
  );
}

type Mode = 'form' | 'yaml';

function PresetEditor({ preset, viewer, onKey }: { preset: Preset; viewer: boolean; onKey: (key: string | null) => void }) {
  const qc = useQueryClient();
  const fields = FORM_FIELDS[preset.kind];
  const [mode, setMode] = useState<Mode>(fields ? 'form' : 'yaml');
  const [name, setName] = useState(preset.name);
  const [data, setData] = useState<Record<string, unknown>>(preset.data ?? {});
  const [text, setText] = useState(() => toYaml(preset.data));
  const [errors, setErrors] = useState<string[]>([]);
  const [armed, setArmed] = useState(false);
  const parsed = useMemo(() => fromYaml(text), [text]);
  const original = useMemo(() => toYaml(preset.data), [preset.data]);
  const dirty = name !== preset.name || (mode === 'yaml' ? text !== original : toYaml(data) !== original);

  // Đổi chế độ: mang dữ liệu sang (YAML hỏng thì không cho rời YAML).
  const switchMode = (m: Mode) => {
    if (m === mode) return;
    if (m === 'form') {
      if (!parsed.ok) return;
      setData(parsed.data);
    } else {
      setText(toYaml(data));
    }
    setMode(m);
  };

  const refresh = () => void qc.invalidateQueries({ queryKey: ['presets'] });
  const save = useMutation({
    mutationFn: async () => {
      const body = mode === 'yaml' ? (parsed.ok ? parsed.data : null) : data;
      if (!body) throw new Error('yaml');
      return (await callOp('preset.update', {
        id: preset.id,
        name: name.trim(),
        data: body,
        expected_updated_at: preset.updated_at ?? null,
      } as never)) as unknown as Preset;
    },
    onSuccess: () => {
      setErrors([]);
      toast({ message: `Đã lưu preset ${preset.name}.`, tone: 'success' });
      refresh();
    },
    onError: (e) => {
      const list = presetErrors(e);
      setErrors(list.length ? list : [errorMessage(e, 'Không lưu được preset.')]);
    },
  });
  const reset = useMutation({
    mutationFn: () => callOp('preset.reset_builtin', { key: preset.key, kind: preset.kind as 'genre' }),
    onSuccess: () => {
      toast({ message: `Đã khôi phục ${preset.name} về bản cài sẵn.`, tone: 'success' });
      refresh();
    },
    onError: (e) => toast({ message: errorMessage(e, 'Không khôi phục được bản gốc.'), tone: 'error' }),
  });
  const dup = useMutation({
    mutationFn: async () => (await callOp('preset.duplicate', { id: preset.id, to_project: false } as never)) as unknown as Preset,
    onSuccess: (p) => {
      toast({ message: `Đã nhân bản thành ${p.key}.`, tone: 'success' });
      refresh();
      onKey(p.key);
    },
    onError: (e) => toast({ message: errorMessage(e, 'Không nhân bản được preset.'), tone: 'error' }),
  });
  const del = useMutation({
    mutationFn: async () => {
      const pv = (await callOp('preset.delete_preview', { id: preset.id })) as unknown as { confirm_token: string };
      return callOp('preset.delete', { confirm_token: pv.confirm_token });
    },
    onSuccess: () => {
      toast({ message: `Đã xoá preset ${preset.name} (hoàn tác ở Lịch sử).`, tone: 'success' });
      refresh();
      onKey(null);
    },
    onError: (e) => toast({ message: errorMessage(e, 'Không xoá được preset.'), tone: 'error' }),
  });

  const onYamlKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key !== 'Tab' || e.shiftKey) return;
    e.preventDefault();
    const el = e.currentTarget;
    const { selectionStart: a, selectionEnd: b } = el;
    const next = `${text.slice(0, a)}  ${text.slice(b)}`;
    setText(next);
    requestAnimationFrame(() => el.setSelectionRange(a + 2, a + 2));
  };

  return (
    <section aria-labelledby="preset-edit-h" className="grid min-w-0 gap-3 rounded-md border border-line-2 bg-bg-1 p-3 md:p-4">
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h2 id="preset-edit-h" className="text-md font-semibold">
            {preset.name}
          </h2>
          <p className="text-xs text-fg-2">
            <span className="font-mono">
              {preset.kind}/{preset.key}
            </span>
            {preset.builtin ? (preset.modified ? ' · cài sẵn, đã sửa so với bản gốc' : ' · cài sẵn') : ' · tự tạo'}
          </p>
        </div>
        {fields ? (
          <SegmentedControl
            label="Cách sửa"
            value={mode}
            onChange={(v) => switchMode(v as Mode)}
            options={[
              { value: 'form', label: 'Form', disabled: mode === 'yaml' && !parsed.ok },
              { value: 'yaml', label: 'YAML' },
            ]}
          />
        ) : null}
      </header>

      <Input id="preset-name" label="Tên" value={name} onChange={(e) => setName(e.target.value)} disabled={viewer} fieldClassName="max-w-md" />

      {mode === 'form' && fields ? (
        <div className="grid gap-3 sm:grid-cols-2 2xl:grid-cols-3">
          {fields.map((f) => (
            <FieldInput key={f.path.join('.')} field={f} value={getPath(data, f.path)} disabled={viewer} onChange={(v) => setData((d) => setPath(d, f.path, v))} />
          ))}
          <p className="text-xs text-fg-3 sm:col-span-2 2xl:col-span-3">
            Trường còn lại (mã hoá, bìa, nguồn tra cứu…) sửa ở chế độ YAML.
          </p>
        </div>
      ) : (
        <div className="grid gap-1">
          <label htmlFor="preset-yaml" className="text-sm font-medium">
            Nội dung (YAML)
          </label>
          <textarea
            id="preset-yaml"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={onYamlKey}
            readOnly={viewer}
            spellCheck={false}
            autoCapitalize="off"
            autoCorrect="off"
            aria-invalid={!parsed.ok || undefined}
            aria-describedby="preset-yaml-msg"
            className={cn(
              'min-h-[16lh] w-full resize-y rounded-sm border border-line-3 bg-bg-2 px-3 py-2 font-mono text-sm leading-[1.6] text-fg-1 [field-sizing:content] [tab-size:2] max-h-[70dvh]',
              !parsed.ok && 'border-state-failed',
              focusRing,
            )}
          />
          <p id="preset-yaml-msg" role={parsed.ok ? undefined : 'alert'} className={cn('text-xs', parsed.ok ? 'text-fg-3' : 'text-state-failed')}>
            {parsed.ok ? 'Tab chèn 2 dấu cách. Thể loại và nền tảng được máy chủ kiểm tra chặt khi lưu.' : (parsed as { error: string }).error}
          </p>
        </div>
      )}

      {errors.length ? (
        <div role="alert" className="grid gap-1 rounded-sm border border-state-failed/50 px-3 py-2 text-sm text-state-failed">
          <p className="font-medium">Máy chủ chưa nhận preset này:</p>
          <ul className="list-disc pl-5 font-mono text-xs">
            {errors.map((e) => (
              <li key={e}>{e}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {viewer ? null : (
        <footer className="flex flex-wrap items-center gap-2 border-t border-line-1 pt-3">
          <Button variant="primary" icon={Save} disabled={!dirty || (mode === 'yaml' && !parsed.ok) || !name.trim()} loading={save.isPending} onClick={() => save.mutate()}>
            Lưu
          </Button>
          {dirty ? (
            <Button
              variant="ghost"
              onClick={() => {
                setName(preset.name);
                setData(preset.data ?? {});
                setText(original);
                setErrors([]);
              }}
            >
              Bỏ thay đổi
            </Button>
          ) : null}
          <span className="flex-1" />
          <Button variant="ghost" icon={Copy} loading={dup.isPending} onClick={() => dup.mutate()}>
            Nhân bản
          </Button>
          {preset.builtin ? (
            <Button icon={RotateCcw} disabled={!preset.modified} loading={reset.isPending} onClick={() => reset.mutate()} title={preset.modified ? undefined : 'Preset đang giống bản cài sẵn.'}>
              Khôi phục bản gốc
            </Button>
          ) : (
            <Button
              variant="danger"
              icon={Trash2}
              armed={armed}
              loading={del.isPending}
              onClick={() => {
                if (armed) {
                  setArmed(false);
                  del.mutate();
                } else {
                  setArmed(true);
                  window.setTimeout(() => setArmed(false), 4000);
                }
              }}
            >
              {armed ? 'Bấm lần nữa để xoá' : 'Xoá'}
            </Button>
          )}
        </footer>
      )}
    </section>
  );
}

function FieldInput({ field, value, disabled, onChange }: { field: FormField; value: unknown; disabled: boolean; onChange: (v: unknown) => void }) {
  const id = `pf-${field.path.join('-')}`;
  const [raw, setRaw] = useState(() => formatField(field.type, value));
  useEffect(() => setRaw(formatField(field.type, value)), [value, field.type]);
  if (field.type === 'enum') {
    return (
      <Select
        id={id}
        label={field.label}
        value={typeof value === 'string' ? value : ''}
        onChange={(v) => onChange(v || undefined)}
        disabled={disabled}
        options={[{ value: '', label: '— không đặt —' }, ...(field.options ?? []).map((o) => ({ value: o, label: o }))]}
      />
    );
  }
  return (
    <Input
      id={id}
      label={field.label}
      hint={field.hint ?? (field.type === 'range' ? 'thấp–cao, vd 60–90' : undefined)}
      inputMode={field.type === 'int' || field.type === 'float' ? 'decimal' : undefined}
      value={raw}
      disabled={disabled}
      onChange={(e) => setRaw(e.target.value)}
      onBlur={() => onChange(parseField(field.type, raw))}
    />
  );
}

function CreatePresetDialog({ open, kind, onClose, onCreated }: { open: boolean; kind: string; onClose: () => void; onCreated: (p: Preset) => void }) {
  const qc = useQueryClient();
  const [key, setKey] = useState('');
  const [name, setName] = useState('');
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (open) {
      setKey('');
      setName('');
      setErr(null);
    }
  }, [open]);
  const m = useMutation({
    mutationFn: async () => (await callOp('preset.create', { kind: kind as 'genre', key, name: name.trim(), data: {}, project_id: null })) as unknown as Preset,
    onSuccess: (p) => {
      toast({ message: `Đã tạo preset ${p.name}.`, tone: 'success' });
      void qc.invalidateQueries({ queryKey: ['presets'] });
      onCreated(p);
      onClose();
    },
    onError: (e) => setErr(presetErrors(e).join(' · ') || errorMessage(e, 'Không tạo được preset.')),
  });
  const strict = kind === 'platform' || kind === 'genre';
  return (
    <Modal
      open={open}
      onClose={m.isPending ? undefined : onClose}
      title={`Tạo preset ${kindLabel(kind).toLowerCase()}`}
      actions={
        <>
          <Button onClick={onClose} disabled={m.isPending}>
            Huỷ
          </Button>
          <Button variant="primary" icon={Plus} loading={m.isPending} disabled={!validPresetKey(key) || !name.trim()} onClick={() => m.mutate()}>
            Tạo preset
          </Button>
        </>
      }
    >
      <div className="grid gap-3">
        <Input id="np-name" label="Tên" value={name} onChange={(e) => setName(e.target.value)} autoFocus />
        <Input
          id="np-key"
          label="Key"
          value={key}
          onChange={(e) => setKey(e.target.value.toLowerCase())}
          hint="Chữ thường, số, gạch ngang/gạch dưới — vd tiktok-cau-chuyen."
          error={key && !validPresetKey(key) ? 'Key chưa hợp lệ.' : (err ?? undefined)}
          className="font-mono"
        />
        {strict ? (
          <p className="text-xs text-fg-2">
            {kindLabel(kind)} có schema chặt: preset trống sẽ bị từ chối. Cách nhanh hơn là nhân bản một preset có sẵn rồi sửa.
          </p>
        ) : null}
      </div>
    </Modal>
  );
}
