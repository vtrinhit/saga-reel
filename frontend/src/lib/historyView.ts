/** Lịch sử phiên bản (Task M): lọc theo actor, gom theo ngày, đọc xung đột hoàn tác. Hàm thuần. */
import type { ChangeSet } from '../api/types';
import { parseServerTime } from './time';

export interface ConflictEntity {
  type: string;
  id: number;
  /** Change set sửa tiếp thực thể (chặn hoàn tác). */
  change_set_id: number;
}

/** `undo_conflict` (409) → danh sách thực thể bị sửa tiếp; lỗi khác → null. */
export function parseConflict(e: unknown): ConflictEntity[] | null {
  const x = e as { code?: string; extra?: { entities?: unknown } } | null;
  if (!x || x.code !== 'undo_conflict') return null;
  const list = Array.isArray(x.extra?.entities) ? x.extra!.entities : [];
  return (list as Record<string, unknown>[])
    .filter((r) => typeof r.type === 'string' && typeof r.id === 'number')
    .map((r) => ({ type: r.type as string, id: r.id as number, change_set_id: Number(r.change_set_id) || 0 }));
}

/** Actor có trong danh sách, "Bạn" (người) trước rồi agent theo tên. */
export function actorsOf(items: Pick<ChangeSet, 'actor'>[]): string[] {
  const set = new Set(items.map((c) => c.actor));
  return [...set].sort((a, b) => Number(a.startsWith('agent:')) - Number(b.startsWith('agent:')) || a.localeCompare(b));
}

/** Khớp actor như `history.list(actor)` của server: kết thúc bằng ':' = tiền tố (vd `agent:` = mọi agent). */
export function actorMatches(actor: string, filter: string): boolean {
  return filter.endsWith(':') ? actor.startsWith(filter) : actor === filter;
}

export function filterHistory(items: ChangeSet[], actor: string | null, entityType: string | null): ChangeSet[] {
  return items.filter(
    (c) => (!actor || actorMatches(c.actor, actor)) && (!entityType || c.entities.some((e) => e.type === entityType)),
  );
}

/** Change set mới nhất của actor (hoặc tiền tố `agent:`) còn hoàn tác được (không tính thao tác hoàn tác/làm lại). */
export function latestUndoable(items: ChangeSet[], actor: string): ChangeSet | null {
  return (
    items.find(
      (c) => actorMatches(c.actor, actor) && c.undone_at == null && c.op_name !== 'history.undo' && c.op_name !== 'history.redo',
    ) ?? null
  );
}

const dayFmt = new Intl.DateTimeFormat('vi-VN', { timeZone: 'Asia/Ho_Chi_Minh', weekday: 'long', day: '2-digit', month: '2-digit', year: 'numeric' });
const keyFmt = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Ho_Chi_Minh', year: 'numeric', month: '2-digit', day: '2-digit' });

/** Gom theo ngày (giờ Việt Nam), giữ thứ tự mới trước: "Hôm nay", "Hôm qua", "thứ hai, 16/11/2026". */
export function groupByDay(items: ChangeSet[], now: number = Date.now()): { key: string; label: string; items: ChangeSet[] }[] {
  const today = keyFmt.format(now);
  const yesterday = keyFmt.format(now - 86_400_000);
  const out: { key: string; label: string; items: ChangeSet[] }[] = [];
  for (const c of items) {
    const t = parseServerTime(c.created_at);
    const key = t == null ? 'unknown' : keyFmt.format(t);
    let g = out.at(-1);
    if (!g || g.key !== key) {
      const label = key === today ? 'Hôm nay' : key === yesterday ? 'Hôm qua' : t == null ? 'Không rõ ngày' : dayFmt.format(t);
      g = { key, label, items: [] };
      out.push(g);
    }
    g.items.push(c);
  }
  return out;
}

const ENTITY_LABEL: Record<string, string> = {
  clip: 'clip',
  sequence: 'tập',
  scene: 'cảnh',
  asset: 'tài sản',
  look: 'look',
  take: 'take',
  bible_item: 'mục bible',
  preset: 'preset',
  template: 'template',
  project: 'dự án',
  dialogue_line: 'câu thoại',
  timeline_item: 'mục timeline',
  sound: 'âm thanh',
  voice_profile: 'giọng',
};

export function entityLabel(type: string, id?: number): string {
  const name = ENTITY_LABEL[type] ?? type.replace(/_/g, ' ');
  return id != null ? `${name} #${id}` : name;
}

/** "3 clip, 1 tập" từ danh sách thực thể. */
export function entitySummary(entities: { type: string }[]): string {
  const m = new Map<string, number>();
  for (const e of entities) m.set(e.type, (m.get(e.type) ?? 0) + 1);
  return [...m].map(([t, n]) => `${n} ${ENTITY_LABEL[t] ?? t}`).join(', ');
}

/**
 * Phiên bản mặc định nên gợi ý khi hoàn tác bị chặn: change set cũ nhất ngay TRƯỚC change set muốn hoàn tác
 * (trạng thái trước thay đổi). Không có → null (thực thể do chính change set đó tạo ra).
 */
export function versionBefore(versions: ChangeSet[], targetId: number): ChangeSet | null {
  return versions.filter((v) => v.id < targetId).sort((a, b) => b.id - a.id)[0] ?? null;
}
