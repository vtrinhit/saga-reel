/**
 * Vá bảng clip theo `entity.changed` thay vì nạp lại cả tập (1.000 clip ≈ 3 MB mỗi lần agent ghi).
 *
 * Sự kiện mang `entities: [[bảng, id], …]` nhưng không nói thêm/xoá/dời. Quy tắc:
 * - không có danh sách thực thể, có `scene` (đổi cảnh = đổi thứ tự), hoặc > `MAX_PATCH` clip → nạp lại cả tập;
 * - còn lại: đọc riêng từng clip đổi (`clip.get` + `take.list` cho take đang chọn / số take) rồi `mergePatch`:
 *   clip trong cache → thay tại chỗ (giữ thứ tự) — trừ khi `rank`/`scene_id` đổi (dời) → nạp lại thứ tự;
 *   clip đã xoá (404) → bỏ khỏi cache; clip mới của tập này → nạp lại; clip của tập khác → bỏ qua.
 */
import { callOp } from '../api/ops';
import type { Clip, Take } from './episodeTypes';

/** Quá số clip này trong một đợt thì nạp lại cả tập rẻ hơn gọi từng clip. */
export const MAX_PATCH = 20;

export type LivePlan = { kind: 'none' } | { kind: 'full' } | { kind: 'patch'; ids: number[] };

/** Từ các cặp `[bảng, id]` gom trong một đợt → cần làm gì với danh sách clip. */
export function planLivePatch(entities: [string, number][] | null): LivePlan {
  if (!entities || entities.length === 0) return { kind: 'full' };
  const ids = new Set<number>();
  for (const [t, id] of entities) {
    if (t === 'scene' || t === 'sequence_order') return { kind: 'full' };
    if (t === 'clip') ids.add(id);
  }
  if (ids.size === 0) return { kind: 'none' };
  if (ids.size > MAX_PATCH) return { kind: 'full' };
  return { kind: 'patch', ids: [...ids] };
}

/** Kết quả đọc lại một clip: bản mới (kèm phần dẫn xuất), `deleted`, hoặc lỗi khác (`unknown`). */
export type Fetched = { id: number; clip: Clip } | { id: number; deleted: true } | { id: number; unknown: true };

/**
 * Gộp các clip đọc lại vào danh sách. Trả `null` khi phải nạp lại cả tập (thứ tự đổi / có clip mới / lỗi không rõ).
 * Giữ nguyên object của clip không đổi (memo của card không vẽ lại).
 */
export function mergePatch(list: Clip[], seqId: number, fetched: Fetched[]): Clip[] | null {
  const byId = new Map(list.map((c, i) => [c.id, i]));
  let out: Clip[] | null = null;
  const removed = new Set<number>();
  for (const f of fetched) {
    const i = byId.get(f.id);
    if ('unknown' in f) return null;
    if ('deleted' in f) {
      if (i != null) removed.add(f.id);
      continue;
    }
    const c = f.clip;
    if (i == null) {
      if (c.sequence_id === seqId && !c.deleted_at) return null; // clip mới của tập này
      continue; // clip của tập khác
    }
    const old = list[i];
    if (c.sequence_id !== seqId || c.deleted_at) {
      removed.add(c.id);
      continue;
    }
    if (c.rank !== old.rank || c.scene_id !== old.scene_id) return null; // dời
    out ??= list.slice();
    out[i] = c;
  }
  if (removed.size) out = (out ?? list).filter((c) => !removed.has(c.id));
  return out ?? list;
}

interface TakeListResult {
  items: Take[];
  selected?: Partial<Record<'image' | 'video', number | null>>;
}

/** Phần dẫn xuất mà `review.queue` kèm theo clip (take đang chọn, số take, media) từ `take.list`. */
export function derivedFromTakes(base: Omit<Clip, 'selected_takes' | 'take_counts'>, takes: TakeListResult): Partial<Clip> {
  const pick = (part: 'image' | 'video') => {
    const id = takes.selected?.[part] ?? (part === 'image' ? base.image_take_id : base.video_take_id);
    return id != null ? (takes.items.find((t) => t.id === id) ?? null) : null;
  };
  const count = (part: 'image' | 'video') => takes.items.filter((t) => t.part === part).length;
  const image = pick('image');
  const video = pick('video');
  return {
    selected_takes: { image, video },
    take_counts: { image: count('image'), video: count('video') },
    image_media_id: image?.media_id ?? null,
    video_media_id: video?.media_id ?? null,
    thumb_media_id: image?.media_id ?? null,
  } as Partial<Clip>;
}

/** Đọc lại một clip theo dạng `review.queue` (clip + take đang chọn + số take). */
export async function fetchClipFull(id: number): Promise<Fetched> {
  try {
    const [c, t] = await Promise.all([
      callOp('clip.get', { id }) as Promise<unknown>,
      callOp('take.list', { owner_type: 'clip', owner_id: id, part: null }) as Promise<unknown>,
    ]);
    const clip = c as Clip & { dialogue?: unknown };
    delete clip.dialogue;
    return { id, clip: { ...clip, ...derivedFromTakes(clip, t as TakeListResult) } };
  } catch (e) {
    const st = (e as { status?: number } | null)?.status;
    return st === 404 ? { id, deleted: true } : { id, unknown: true };
  }
}
