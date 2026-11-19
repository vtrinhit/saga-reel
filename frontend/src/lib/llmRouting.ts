/**
 * Định tuyến LLM (ops `llm.*` của Task 13). Op chưa có trong `schema.d.ts` lúc sinh kiểu → mọi chỗ đọc payload nằm
 * ở đây, viết theo bản `ops/llm_ops.py` của Task 13 (đang gộp song song) và chịu được biến thể nhỏ:
 * - `llm.task_list` → `{items: [{key, label, description, template_key, route: {connection, model, params},
 *   fallbacks: [route], effective_routes: [route]}]}`
 * - `llm.models({connection})` → `{connection, models: [string | {id|name}]}`
 * - `llm.task_update({key, route, fallbacks})` — `route: null` = về mặc định.
 */

export interface LlmRoute {
  connection: string | null;
  model: string | null;
  params: Record<string, unknown>;
}

export interface LlmTask {
  key: string;
  label: string;
  description: string;
  route: LlmRoute;
  fallbacks: LlmRoute[];
  effective: LlmRoute[];
}

const obj = (v: unknown): Record<string, unknown> => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {});
const strOrNull = (v: unknown): string | null => (typeof v === 'string' && v ? v : typeof v === 'number' ? String(v) : null);

export function normalizeRoute(v: unknown): LlmRoute {
  const r = obj(v);
  return { connection: strOrNull(r.connection), model: strOrNull(r.model), params: obj(r.params) };
}

export function normalizeTasks(raw: unknown): LlmTask[] {
  const items = Array.isArray(raw) ? raw : Array.isArray(obj(raw).items) ? (obj(raw).items as unknown[]) : [];
  return items
    .map((it) => {
      const t = obj(it);
      const key = strOrNull(t.key) ?? strOrNull(t.task) ?? strOrNull(t.name);
      if (!key) return null;
      const route = t.route !== undefined ? normalizeRoute(t.route) : normalizeRoute(t);
      return {
        key,
        label: strOrNull(t.label) ?? key,
        description: typeof t.description === 'string' ? t.description : '',
        route,
        fallbacks: Array.isArray(t.fallbacks) ? t.fallbacks.map(normalizeRoute) : [],
        effective: Array.isArray(t.effective_routes) ? t.effective_routes.map(normalizeRoute) : [],
      } satisfies LlmTask;
    })
    .filter((t): t is LlmTask => t != null);
}

export function normalizeModels(raw: unknown): string[] {
  const list = Array.isArray(raw) ? raw : Array.isArray(obj(raw).models) ? (obj(raw).models as unknown[]) : [];
  const out: string[] = [];
  for (const m of list) {
    const id = typeof m === 'string' ? m : strOrNull(obj(m).id) ?? strOrNull(obj(m).name);
    if (id && !out.includes(id)) out.push(id);
  }
  return out;
}

/** Tham số dạng JSON (đối tượng) từ ô nhập; trống = `{}`. */
export function parseParams(text: string): { ok: true; value: Record<string, unknown> } | { ok: false; error: string } {
  const t = text.trim();
  if (!t) return { ok: true, value: {} };
  try {
    const v = JSON.parse(t);
    if (!v || typeof v !== 'object' || Array.isArray(v)) return { ok: false, error: 'Tham số phải là đối tượng JSON, vd {"temperature": 0.7}.' };
    return { ok: true, value: v as Record<string, unknown> };
  } catch {
    return { ok: false, error: 'Tham số không phải JSON hợp lệ.' };
  }
}

export interface RouteDraft {
  connection: string;
  model: string;
  params: string;
}

export const routeToDraft = (r: LlmRoute): RouteDraft => ({
  connection: r.connection ?? '',
  model: r.model ?? '',
  params: Object.keys(r.params).length ? JSON.stringify(r.params, null, 2) : '',
});

/**
 * Bản nháp → params `llm.task_update`. Route chính không có kết nối và không có gì khác = `null` (về mặc định).
 * Route dự phòng bắt buộc có kết nối (server cũng từ chối).
 */
export function buildTaskUpdate(
  key: string,
  route: RouteDraft,
  fallbacks: RouteDraft[],
): { ok: true; params: Record<string, unknown> } | { ok: false; errors: Record<string, string> } {
  const errors: Record<string, string> = {};
  const conv = (d: RouteDraft, at: string) => {
    const p = parseParams(d.params);
    if ('error' in p) errors[`${at}.params`] = p.error;
    return { connection: d.connection || null, model: d.model.trim() || null, params: 'value' in p ? p.value : {} };
  };
  const main = conv(route, 'route');
  const fbs = fallbacks.map((f, i) => {
    if (!f.connection) errors[`fallbacks.${i}.connection`] = 'Chọn kết nối cho route dự phòng.';
    return conv(f, `fallbacks.${i}`);
  });
  if (Object.keys(errors).length) return { ok: false, errors };
  const empty = !main.connection && !main.model && Object.keys(main.params).length === 0;
  return { ok: true, params: { key, route: empty ? null : main, fallbacks: fbs } };
}

/** "temperature=0.7 · max_tokens=2000" */
export function paramsText(p: Record<string, unknown>): string {
  return Object.entries(p)
    .map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : String(v)}`)
    .join(' · ');
}
