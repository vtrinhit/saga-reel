/** Thời gian phía server (ISO 8601, UTC) → ms epoch, và định dạng theo giờ Việt Nam (DESIGN §8). */

const VN_TZ = 'Asia/Ho_Chi_Minh';

/**
 * ISO của server → ms epoch. Server luôn gửi kèm múi giờ (`+00:00`); chuỗi không có múi giờ (dữ liệu cũ, SQLite)
 * được coi là **UTC** chứ không phải giờ máy, nếu không ETA lệch 7 giờ. Không đọc được → null.
 */
export function parseServerTime(v: unknown): number | null {
  if (typeof v !== 'string' || !v.trim()) return null;
  let s = v.trim();
  const hasTime = /T|\s\d{2}:/.test(s);
  if (hasTime && !/(Z|[+-]\d{2}:?\d{2})$/i.test(s)) s = `${s.replace(' ', 'T')}Z`;
  const t = Date.parse(s);
  return Number.isNaN(t) ? null : t;
}

const partsFmt = new Intl.DateTimeFormat('en-GB', {
  timeZone: VN_TZ,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hourCycle: 'h23',
});

function vnParts(ms: number) {
  const p: Record<string, string> = {};
  for (const x of partsFmt.formatToParts(new Date(ms))) p[x.type] = x.value;
  return p;
}

/** "19/11/2026 14:05" (24 giờ, giờ Việt Nam). */
export function formatDateTime(ms: number): string {
  const p = vnParts(ms);
  return `${p.day}/${p.month}/${p.year} ${p.hour}:${p.minute}`;
}

/** "14:05:09" — giờ trong log. */
export function formatClock(ms: number): string {
  const p = vnParts(ms);
  return `${p.hour}:${p.minute}:${p.second}`;
}

/** "vừa xong" · "12 giây trước" · "3 phút trước" · "2 giờ trước" · "hôm qua 21:40" · quá 7 ngày → ngày tuyệt đối. */
export function formatRelative(ms: number, now: number = Date.now()): string {
  const diff = Math.round((now - ms) / 1000);
  if (diff < 5 && diff > -60) return 'vừa xong';
  if (diff < 0) return formatDateTime(ms);
  if (diff < 60) return `${diff} giây trước`;
  if (diff < 3600) return `${Math.floor(diff / 60)} phút trước`;
  const a = vnParts(ms);
  const b = vnParts(now);
  const dayKey = (p: Record<string, string>) => Date.UTC(+p.year, +p.month - 1, +p.day);
  const days = Math.round((dayKey(b) - dayKey(a)) / 86_400_000);
  if (days === 0) return `${Math.floor(diff / 3600)} giờ trước`;
  if (days === 1) return `hôm qua ${a.hour}:${a.minute}`;
  if (days <= 7) return `${days} ngày trước`;
  return formatDateTime(ms);
}

/** Như `formatRelative` nhưng nhận chuỗi server; không đọc được → "—". */
export function relativeFromServer(v: unknown, now?: number): string {
  const t = parseServerTime(v);
  return t == null ? '—' : formatRelative(t, now);
}
