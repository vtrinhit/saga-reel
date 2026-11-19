/**
 * Danh sách job: phân trang theo cursor (`job.list`, id giảm dần) + gộp sự kiện `job.updated` realtime vào các
 * trang đã tải (react-query `useInfiniteQuery`). Thuần, không có React.
 */
import type { Job, JobLog, JobPage } from '../api/types';
import { parseServerTime } from './time';

/**
 * Bản nào của cùng một job được giữ khi gộp (sự kiện `job.updated` ↔ kết quả `job.list`/`job.get` về muộn):
 * bản đến sau thắng, **trừ khi** nó cũ hơn theo `updated_at` **và** khác trạng thái — vd. response tải từ trước khi
 * job xong ("running") về sau sự kiện "succeeded" thì không được ghi đè trạng thái kết thúc.
 * Không so riêng `updated_at` khi cùng trạng thái: sự kiện tiến độ của worker mang `updated_at` của lúc giành job
 * (ảnh chụp `_data`), nên tiến độ mới hơn có thể mang `updated_at` cũ hơn bản đã tải.
 */
export function pickJob(cur: Job | undefined, inc: Job): Job {
  if (!cur) return inc;
  const a = parseServerTime(cur.updated_at);
  const b = parseServerTime(inc.updated_at);
  if (a != null && b != null && b < a && inc.status !== cur.status) return cur;
  return inc;
}

/** Áp các bản đã biết từ sự kiện (mới hơn theo `pickJob`) lên một trang vừa tải. Không đổi gì → trả lại mảng cũ. */
export function overlayKnown(items: Job[], known: ReadonlyMap<number, Job>): Job[] {
  let changed = false;
  const out = items.map((j) => {
    const k = known.get(j.id);
    if (!k) return j;
    const keep = pickJob(k, j); // trang tải về đến sau sự kiện
    if (keep !== j) changed = true;
    return keep;
  });
  return changed ? out : items;
}

export interface JobFilters {
  status?: string | null;
  type?: string | null;
  lane?: string | null;
}

export function toListParams(f: JobFilters, cursor: number | undefined, limit: number) {
  const out: { status?: string; type?: string; lane?: string; cursor?: number; limit: number } = { limit };
  if (f.status) out.status = f.status;
  if (f.type) out.type = f.type;
  if (f.lane) out.lane = f.lane;
  if (cursor != null) out.cursor = cursor;
  return out;
}

export function matchesFilters(j: Job, f: JobFilters): boolean {
  return (!f.status || j.status === f.status) && (!f.type || j.type === f.type) && (!f.lane || j.lane === f.lane);
}

/** Nối các trang, bỏ trùng theo id (giữ bản ở trang trước), sắp id giảm dần. */
export function flattenPages(pages: JobPage[]): Job[] {
  const seen = new Set<number>();
  const out: Job[] = [];
  for (const p of pages) {
    for (const j of p.items) {
      if (seen.has(j.id)) continue;
      seen.add(j.id);
      out.push(j);
    }
  }
  return out.sort((a, b) => b.id - a.id);
}

/**
 * Gộp một job (payload `job.updated` = `job_dict` đầy đủ) vào các trang đã tải:
 * - đã có → thay tại chỗ (kể cả khi không còn khớp lọc: hàng không biến mất khi đang xem; tải lại sẽ lọc đúng);
 * - chưa có, khớp lọc: mới hơn mọi job đã tải → đầu trang 1; nằm trong khoảng đã tải → chèn đúng chỗ; cũ hơn job
 *   cuối mà còn trang sau → bỏ qua (sẽ có khi tải tiếp), đã tải hết → thêm cuối.
 * Không đổi gì → trả lại đúng mảng cũ.
 */
export function mergeJobUpdate(pages: JobPage[], j: Job, f: JobFilters): JobPage[] {
  if (pages.length === 0) return pages;
  for (let pi = 0; pi < pages.length; pi++) {
    const idx = pages[pi].items.findIndex((x) => x.id === j.id);
    if (idx >= 0) {
      const keep = pickJob(pages[pi].items[idx], j);
      if (keep !== j) return pages; // bản cũ hơn (khác trạng thái): bỏ qua
      const items = pages[pi].items.slice();
      items[idx] = j;
      const next = pages.slice();
      next[pi] = { ...pages[pi], items };
      return next;
    }
  }
  if (!matchesFilters(j, f)) return pages;

  // Trang chứa khoảng id của job: trang đầu tiên có job cuối cùng nhỏ hơn id này.
  for (let pi = 0; pi < pages.length; pi++) {
    const items = pages[pi].items;
    const last = items[items.length - 1];
    const isLastPage = pi === pages.length - 1;
    if ((last && last.id < j.id) || (isLastPage && pages[pi].next_cursor == null)) {
      const at = items.findIndex((x) => x.id < j.id);
      const nextItems = items.slice();
      nextItems.splice(at < 0 ? items.length : at, 0, j);
      const next = pages.slice();
      next[pi] = { ...pages[pi], items: nextItems };
      return next;
    }
  }
  return pages;
}

/** Gộp log (bỏ trùng id, sắp tăng dần). Không có dòng mới → trả lại mảng cũ. */
export function mergeLogs(existing: JobLog[], incoming: JobLog[]): JobLog[] {
  const have = new Set(existing.map((l) => l.id));
  const fresh = incoming.filter((l) => {
    if (have.has(l.id)) return false;
    have.add(l.id);
    return true;
  });
  if (fresh.length === 0) return existing;
  return [...existing, ...fresh].sort((a, b) => a.id - b.id);
}
