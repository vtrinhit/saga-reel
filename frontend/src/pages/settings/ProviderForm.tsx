import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Eye, EyeOff, Save } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { toApiError, type ApiError } from '../../api/client';
import { qk } from '../../api/hooks';
import { callOp } from '../../api/ops';
import type { Adapter, ConfigField, Connection, ProviderKind } from '../../api/types';
import { Button } from '../../components/ui/Button';
import { Checkbox } from '../../components/ui/Checkbox';
import { Drawer } from '../../components/ui/Drawer';
import { IconButton } from '../../components/ui/IconButton';
import { Input } from '../../components/ui/Input';
import { Select } from '../../components/ui/Select';
import { Textarea } from '../../components/ui/Textarea';
import { buildSubmit, fieldPlaceholder, initialValues, serverFieldErrors, type FormValues } from '../../lib/providerForm';
import { toast } from '../../stores/toast';
import { KIND_LABEL, KIND_ORDER } from './providerText';

export interface ProviderFormProps {
  open: boolean;
  onClose: () => void;
  adapters: Adapter[];
  /** Sửa kết nối này; không có = thêm mới. */
  conn?: Connection | null;
  /** Thêm mới: loại chọn sẵn. */
  kind?: ProviderKind | null;
}

/** Drawer thêm/sửa kết nối: form sinh từ `config_fields` của adapter. */
export function ProviderForm({ open, onClose, adapters, conn, kind }: ProviderFormProps) {
  const qc = useQueryClient();
  const editing = !!conn;
  const [kindSel, setKindSel] = useState<ProviderKind>(conn?.kind ?? kind ?? 'image');
  const [adapterId, setAdapterId] = useState<string | null>(conn?.adapter ?? null);
  const adapter = useMemo(() => adapters.find((a) => a.id === adapterId) ?? null, [adapters, adapterId]);
  const [values, setValues] = useState<FormValues | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [serverError, setServerError] = useState<ApiError | null>(null);
  const [shown, setShown] = useState<Record<string, boolean>>({});

  // Mở lại / đổi kết nối đang sửa → nạp lại form. Đóng → xoá mọi giá trị đã gõ (kể cả khoá API) khỏi state.
  useEffect(() => {
    if (!open) {
      setValues(null);
      setShown({});
      setServerError(null);
      return;
    }
    setKindSel(conn?.kind ?? kind ?? 'image');
    setAdapterId(conn?.adapter ?? null);
    setErrors({});
    setServerError(null);
    setShown({});
  }, [open, conn, kind]);

  // Chọn adapter → giá trị đầu của form.
  useEffect(() => {
    if (!open) return;
    setValues(adapter ? initialValues(adapter, conn ?? undefined) : null);
    setErrors({});
  }, [open, adapter, conn]);

  const kindAdapters = adapters.filter((a) => a.kind === kindSel);

  // Khoá API không đi qua `variables` của mutation (cache mutation giữ chúng vài phút): mutationFn đọc giá trị
  // hiện tại qua ref; kết quả trả về là kết nối đã che khoá.
  const valuesRef = useRef(values);
  valuesRef.current = values;
  const save = useMutation({
    mutationFn: async () => {
      const v = valuesRef.current;
      if (!adapter || !v) return null;
      const r = buildSubmit(adapter, v, conn ?? undefined);
      setErrors(r.errors);
      if (r.update) return (await callOp('provider.update', r.update as never)) as Connection;
      if (r.create) return (await callOp('provider.create', r.create as never)) as Connection;
      return null;
    },
    onSuccess: (saved) => {
      if (!saved) return;
      setValues(null);
      void qc.invalidateQueries({ queryKey: qk.connections });
      toast({ tone: 'success', message: editing ? `Đã lưu kết nối ${saved.name}.` : `Đã thêm kết nối ${saved.name}.` });
      onClose();
    },
    onError: (e) => {
      const err = toApiError(e);
      setServerError(err);
      const bad = serverFieldErrors(err.extra);
      if (bad.length) setErrors((cur) => ({ ...cur, ...Object.fromEntries(bad.map((k) => [k, err.detail])) }));
    },
  });

  const set = (name: string, v: string | boolean) =>
    setValues((cur) => (cur ? { ...cur, fields: { ...cur.fields, [name]: v } } : cur));

  const fieldId = (name: string) => `pf-${name}`;

  const renderField = (f: ConfigField) => {
    if (!values) return null;
    const v = values.fields[f.name];
    const common = {
      id: fieldId(f.name),
      label: f.required ? f.label : `${f.label} (tuỳ chọn)`,
      hint: f.help || undefined,
      error: errors[f.name],
    };
    switch (f.type) {
      case 'bool':
        return (
          <div key={f.name} className="grid gap-1">
            <Checkbox label={f.label} checked={v === true} onChange={(e) => set(f.name, e.target.checked)} />
            {f.help ? <p className="text-xs text-fg-3">{f.help}</p> : null}
          </div>
        );
      case 'enum':
        return (
          <Select
            key={f.name}
            {...common}
            value={typeof v === 'string' ? v : ''}
            onChange={(nv) => set(f.name, nv)}
            options={[
              ...(f.required ? [] : [{ value: '', label: `Mặc định (${String(f.default ?? '—')})` }]),
              ...(f.choices ?? []).map((c) => ({ value: String(c), label: String(c) })),
            ]}
          />
        );
      case 'json':
        return (
          <Textarea
            key={f.name}
            {...common}
            value={typeof v === 'string' ? v : ''}
            onChange={(e) => set(f.name, e.target.value)}
            placeholder={fieldPlaceholder(f, conn ?? undefined)}
            className="font-mono text-sm md:text-sm"
            minRows={3}
            maxRows={10}
            spellCheck={false}
          />
        );
      case 'secret':
        return (
          <Input
            key={f.name}
            {...common}
            type={shown[f.name] ? 'text' : 'password'}
            autoComplete="new-password"
            spellCheck={false}
            value={typeof v === 'string' ? v : ''}
            onChange={(e) => set(f.name, e.target.value)}
            placeholder={fieldPlaceholder(f, conn ?? undefined)}
            trailing={
              <IconButton
                icon={shown[f.name] ? EyeOff : Eye}
                label={shown[f.name] ? 'Ẩn khoá' : 'Hiện khoá đang gõ'}
                noTip
                pressed={!!shown[f.name]}
                onClick={() => setShown((s) => ({ ...s, [f.name]: !s[f.name] }))}
                className="h-full w-11"
              />
            }
          />
        );
      default:
        return (
          <Input
            key={f.name}
            {...common}
            type={f.type === 'url' ? 'url' : 'text'}
            inputMode={f.type === 'int' ? 'numeric' : undefined}
            spellCheck={false}
            value={typeof v === 'string' ? v : ''}
            onChange={(e) => set(f.name, e.target.value)}
            placeholder={fieldPlaceholder(f, conn ?? undefined)}
            className={f.type === 'int' ? 'font-mono tnum' : undefined}
          />
        );
    }
  };

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title={editing ? `Sửa kết nối ${conn!.name}` : 'Thêm kết nối'}
      footer={
        <>
          <Button onClick={onClose}>Huỷ</Button>
          <Button
            variant="primary"
            icon={Save}
            loading={save.isPending}
            disabled={!values}
            onClick={() => save.mutate()}
          >
            {editing ? 'Lưu' : 'Thêm kết nối'}
          </Button>
        </>
      }
    >
      <form
        className="grid gap-4"
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          if (values) save.mutate();
        }}
      >
        {editing ? (
          <p className="text-sm text-fg-2">
            {KIND_LABEL[conn!.kind]} · {adapters.find((a) => a.id === conn!.adapter)?.label ?? conn!.adapter}
          </p>
        ) : (
          <>
            <Select
              id="pf-kind"
              label="Loại"
              value={kindSel}
              onChange={(k) => {
                setKindSel(k);
                setAdapterId(null);
              }}
              options={KIND_ORDER.map((k) => ({ value: k, label: KIND_LABEL[k] }))}
            />
            <Select
              id="pf-adapter"
              label="Provider"
              value={adapterId}
              onChange={setAdapterId}
              placeholder="Chọn provider…"
              options={kindAdapters.map((a) => ({ value: a.id, label: a.label, description: a.id }))}
            />
          </>
        )}

        {values && adapter ? (
          <>
            <Input
              id="pf-name"
              label="Tên kết nối"
              hint="Tên dùng trong Cài đặt (vd image.connection) và lịch sử."
              value={values.name}
              onChange={(e) => setValues({ ...values, name: e.target.value })}
              error={errors.name}
              autoComplete="off"
              data-autofocus
            />
            {adapter.config_fields.map(renderField)}
            <Input
              id="pf-concurrency"
              label="Số việc chạy song song"
              hint="Số việc chạy cùng lúc trên kết nối này (1–32)."
              inputMode="numeric"
              value={values.concurrency}
              onChange={(e) => setValues({ ...values, concurrency: e.target.value })}
              error={errors.concurrency}
              className="font-mono tnum"
            />
            <Checkbox
              label="Bật kết nối"
              checked={values.enabled}
              onChange={(e) => setValues({ ...values, enabled: e.target.checked })}
            />
          </>
        ) : null}

        {serverError ? (
          <p role="alert" className="text-sm text-state-failed">
            {serverError.detail}
            {serverError.hint && !/extra\.errors/.test(serverError.hint) ? ` ${serverError.hint}` : ''}
          </p>
        ) : null}
        {/* Enter trong ô nhập gửi form */}
        <button type="submit" hidden aria-hidden tabIndex={-1} />
      </form>
    </Drawer>
  );
}
