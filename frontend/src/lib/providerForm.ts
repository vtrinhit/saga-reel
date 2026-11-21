/**
 * Form thêm/sửa kết nối provider sinh từ `config_fields` của adapter (`provider.adapters`). Thuần, không có React.
 *
 * - Trường `base_url` / `api_key` / `default_model` là cột của kết nối; trường khác nằm trong `options`.
 * - `name`, `concurrency`, `enabled` không thuộc `config_fields` (form luôn có).
 * - Khoá API (`secret`) không bao giờ được đưa vào ô nhập: ô để trống, placeholder hiện dạng che; trống khi sửa =
 *   giữ nguyên (không gửi).
 * - Tuỳ chọn để trống = dùng mặc định của adapter (không gửi khi thêm; khi sửa thì xoá khoá khỏi `options`).
 *   `provider.update` thay **toàn bộ** `options`, nên bản sửa đè lên options hiện có để giữ các khoá form không biết.
 */
import type { Adapter, ConfigField, Connection } from '../api/types';

export const COLUMN_FIELDS = ['base_url', 'api_key', 'default_model'] as const;
const isColumn = (name: string) => (COLUMN_FIELDS as readonly string[]).includes(name);

export type FieldValue = string | boolean;

export interface FormValues {
  name: string;
  /** Chuỗi để ô số giữ được giá trị đang gõ dở. */
  concurrency: string;
  enabled: boolean;
  fields: Record<string, FieldValue>;
}

export interface CreateParams {
  kind: Adapter['kind'];
  adapter: string;
  name: string;
  base_url: string;
  api_key: string;
  default_model: string;
  options: Record<string, unknown>;
  concurrency: number;
  enabled: boolean;
}

export interface UpdateParams {
  id: number;
  fields: {
    name: string;
    base_url: string;
    default_model: string;
    api_key?: string;
    options: Record<string, unknown>;
    concurrency: number;
    enabled: boolean;
  };
}

export interface SubmitResult {
  errors: Record<string, string>;
  create?: CreateParams;
  update?: UpdateParams;
}

/**
 * Trường danh sách giây (`durations`, kiểu json danh sách số): hiện và nhập dạng "3, 5, 6" thay vì JSON.
 * Để trống = mức mặc định của adapter (`default` rỗng = mọi số giây).
 */
export function isSecondsList(f: ConfigField): boolean {
  return f.type === 'json' && f.name === 'durations' && Array.isArray(f.default ?? []);
}

export const secondsListText = (v: unknown): string => (Array.isArray(v) ? v.join(', ') : '');

/** "3, 5 6;8" → [3, 5, 6, 8] (số nguyên dương, tăng dần, không trùng); sai → lỗi. */
export function parseSecondsList(text: string): number[] | null {
  const parts = text.split(/[\s,;]+/).filter(Boolean);
  const out = new Set<number>();
  for (const p of parts) {
    if (!/^\d+$/.test(p) || Number(p) <= 0) return null;
    out.add(Number(p));
  }
  return [...out].sort((a, b) => a - b);
}

const show = (v: unknown): string => {
  if (v == null) return '';
  if (typeof v === 'object') return JSON.stringify(v, null, 2);
  return String(v);
};

function initialField(f: ConfigField, conn?: Connection): FieldValue {
  if (f.type === 'secret') return '';
  if (f.type === 'bool') {
    const v = conn && f.name in conn.options ? conn.options[f.name] : f.default;
    return v === true;
  }
  if (isColumn(f.name)) {
    if (conn) return show((conn as unknown as Record<string, unknown>)[f.name]);
    // thêm mới: địa chỉ máy chủ điền sẵn mặc định (thường là bắt buộc); model để trống = mặc định của adapter
    return f.name === 'base_url' ? show(f.default) : '';
  }
  if (conn && f.name in conn.options) return isSecondsList(f) ? secondsListText(conn.options[f.name]) : show(conn.options[f.name]);
  return '';
}

export function initialValues(adapter: Adapter, conn?: Connection): FormValues {
  return {
    name: conn?.name ?? '',
    concurrency: String(conn?.concurrency ?? 2),
    enabled: conn?.enabled ?? true,
    fields: Object.fromEntries(adapter.config_fields.map((f) => [f.name, initialField(f, conn)])),
  };
}

/** Gợi ý trong ô: khoá đã che + "để trống = giữ nguyên"; tuỳ chọn trống → "Mặc định: …". */
export function fieldPlaceholder(f: ConfigField, conn?: Connection): string | undefined {
  if (f.type === 'secret') {
    if (conn?.has_api_key) return `${conn.api_key} — để trống = giữ nguyên`;
    return f.required ? undefined : 'Để trống nếu không cần khoá';
  }
  if (f.type === 'bool' || f.type === 'enum') return undefined;
  if (isSecondsList(f)) {
    const d = secondsListText(f.default);
    return d ? `Mặc định: ${d}` : 'Để trống = mọi số giây';
  }
  if (f.default == null || f.default === '') return undefined;
  if (f.type === 'json') {
    const d = JSON.stringify(f.default);
    return d === '{}' || d === '[]' ? undefined : `Mặc định: ${d}`;
  }
  return `Mặc định: ${show(f.default)}`;
}

type Parsed = { ok: true; value: unknown; empty: boolean } | { ok: false; error: string };

function parseField(f: ConfigField, raw: FieldValue): Parsed {
  if (f.type === 'bool') return { ok: true, value: raw === true, empty: false };
  const s = typeof raw === 'string' ? raw : '';
  const t = s.trim();
  // Khoá API gửi nguyên văn (không trim); trường khác bỏ khoảng trắng hai đầu.
  if (f.type === 'secret') return { ok: true, value: s, empty: t === '' };
  if (t === '') return { ok: true, value: undefined, empty: true };
  switch (f.type) {
    case 'int': {
      if (!/^-?\d+$/.test(t)) return { ok: false, error: `${f.label} phải là số nguyên.` };
      return { ok: true, value: Number(t), empty: false };
    }
    case 'url':
      if (!/^https?:\/\/\S+$/i.test(t)) return { ok: false, error: 'Địa chỉ phải bắt đầu bằng http:// hoặc https://.' };
      return { ok: true, value: t, empty: false };
    case 'json': {
      if (isSecondsList(f)) {
        const list = parseSecondsList(t);
        if (!list) return { ok: false, error: `${f.label}: nhập các số giây nguyên dương, cách nhau bằng dấu phẩy.` };
        return { ok: true, value: list, empty: false };
      }
      let v: unknown;
      try {
        v = JSON.parse(t);
      } catch {
        return { ok: false, error: `${f.label} không phải JSON hợp lệ.` };
      }
      if (Array.isArray(f.default) && !Array.isArray(v)) {
        return { ok: false, error: `${f.label} phải là danh sách JSON ([…]).` };
      }
      if (f.default && typeof f.default === 'object' && !Array.isArray(f.default) && (typeof v !== 'object' || v == null || Array.isArray(v))) {
        return { ok: false, error: `${f.label} phải là đối tượng JSON ({…}).` };
      }
      return { ok: true, value: v, empty: false };
    }
    case 'enum': {
      const choice = (f.choices ?? []).find((c) => String(c) === t);
      if (choice === undefined) return { ok: false, error: `Chọn một giá trị cho ${f.label}.` };
      return { ok: true, value: choice, empty: false };
    }
    default:
      return { ok: true, value: t, empty: false };
  }
}

export function buildSubmit(adapter: Adapter, values: FormValues, conn?: Connection): SubmitResult {
  const errors: Record<string, string> = {};
  const name = values.name.trim();
  if (!name) errors.name = 'Nhập tên kết nối.';
  const concurrency = Number(values.concurrency.trim());
  if (!/^\d+$/.test(values.concurrency.trim()) || concurrency < 1 || concurrency > 32) {
    errors.concurrency = 'Số việc song song từ 1 đến 32.';
  }

  const columns: Record<string, string> = { base_url: '', default_model: '' };
  let apiKey: string | undefined;
  const options: Record<string, unknown> = conn ? { ...conn.options } : {};

  for (const f of adapter.config_fields) {
    const p = parseField(f, values.fields[f.name] ?? (f.type === 'bool' ? false : ''));
    if ('error' in p) {
      errors[f.name] = p.error;
      continue;
    }
    if (f.type === 'secret') {
      if (p.empty) {
        if (f.required && !conn?.has_api_key) errors[f.name] = `Nhập ${f.label}.`;
      } else apiKey = p.value as string;
      continue;
    }
    if (f.required && p.empty) {
      errors[f.name] = `Nhập ${f.label}.`;
      continue;
    }
    if (isColumn(f.name)) {
      columns[f.name] = p.empty ? '' : String(p.value);
    } else if (p.empty) {
      delete options[f.name];
    } else {
      options[f.name] = p.value;
    }
  }

  if (Object.keys(errors).length) return { errors };

  if (conn) {
    const fields: UpdateParams['fields'] = {
      name,
      base_url: columns.base_url,
      default_model: columns.default_model,
      options,
      concurrency,
      enabled: values.enabled,
    };
    if (apiKey !== undefined) fields.api_key = apiKey;
    return { errors, update: { id: conn.id, fields } };
  }
  return {
    errors,
    create: {
      kind: adapter.kind,
      adapter: adapter.id,
      name,
      base_url: columns.base_url,
      api_key: apiKey ?? '',
      default_model: columns.default_model,
      options,
      concurrency,
      enabled: values.enabled,
    },
  };
}

/** Lỗi `validation_failed` của server (`extra.errors[].loc`) → tên trường để tô đỏ (không lấy `msg` tiếng Anh). */
export function serverFieldErrors(extra: Record<string, unknown>): string[] {
  const errs = Array.isArray(extra.errors) ? (extra.errors as { loc?: unknown[] }[]) : [];
  const out: string[] = [];
  for (const e of errs) {
    const loc = Array.isArray(e.loc) ? e.loc.map(String) : [];
    const key = loc[0] === 'options' || loc[0] === 'fields' ? (loc[1] === 'options' ? loc[2] : loc[1]) : loc[loc.length - 1];
    if (key && !out.includes(key)) out.push(key);
  }
  return out;
}
