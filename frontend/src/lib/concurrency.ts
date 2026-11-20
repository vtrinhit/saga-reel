/** Luồng: trần số việc chạy cùng lúc theo loại provider (op `queue.concurrency` / `queue.set_concurrency`). */

export type ConcurrencyKind = 'image' | 'video' | 'llm' | 'tts' | 'stt' | 'render';

export interface LaneUsage {
  lane: string;
  connection_id: number | null;
  name: string;
  enabled: boolean;
  cap: number;
  running: number;
  queued: number;
}

export interface KindUsage {
  kind: ConcurrencyKind;
  label: string;
  key: string;
  cap: number;
  running: number;
  queued: number;
  connections: LaneUsage[];
}

export interface ConcurrencyOverview {
  kinds: KindUsage[];
  min: number;
  max: number;
  paused: boolean;
}

export const CAP_MIN = 1;
export const CAP_MAX = 20;

/** Một câu ngắn cho mỗi loại: luồng ấy gánh việc gì. */
export const KIND_HINTS: Record<ConcurrencyKind, string> = {
  image: 'Tạo ảnh clip, sheet nhân vật, chạy thử ảnh.',
  video: 'Tạo video từ ảnh, chạy thử video.',
  llm: 'Tách cảnh, viết truyện, dịch lồng tiếng, phân tích kênh.',
  tts: 'Đọc thoại thành giọng.',
  stt: 'Nhận dạng lời thoại từ âm thanh.',
  render: 'Dựng tập, ghép âm thanh, xuất gói (ffmpeg).',
};

export function clampCap(n: number): number {
  if (!Number.isFinite(n)) return CAP_MIN;
  return Math.max(CAP_MIN, Math.min(CAP_MAX, Math.round(n)));
}

/** Gộp giá trị đang sửa (chưa ghi) vào overview để giao diện phản ánh ngay (optimistic). */
export function withPending(data: ConcurrencyOverview, pending: Partial<Record<ConcurrencyKind, number>>): ConcurrencyOverview {
  if (!Object.keys(pending).length) return data;
  return { ...data, kinds: data.kinds.map((k) => (pending[k.kind] != null ? { ...k, cap: pending[k.kind]! } : k)) };
}

/** Tóm tắt cho đầu trang Việc: tổng đang chạy / tổng trần, và các loại đang bận (chạy hoặc chờ). */
export function summarizeUsage(data: ConcurrencyOverview): {
  running: number;
  cap: number;
  busy: { kind: ConcurrencyKind; label: string; running: number; cap: number; queued: number }[];
} {
  let running = 0;
  let cap = 0;
  const busy = [];
  for (const k of data.kinds) {
    running += k.running;
    cap += k.cap;
    if (k.running > 0 || k.queued > 0) busy.push({ kind: k.kind, label: k.label, running: k.running, cap: k.cap, queued: k.queued });
  }
  return { running, cap, busy };
}

/** Câu trạng thái của một loại, vd "2/3 đang chạy · 5 chờ" hoặc "Rảnh". */
export function usageText(k: Pick<KindUsage, 'running' | 'cap' | 'queued'>): string {
  if (k.running === 0 && k.queued === 0) return 'Rảnh';
  const parts = [`${k.running}/${k.cap} đang chạy`];
  if (k.queued > 0) parts.push(`${k.queued} chờ`);
  return parts.join(', ');
}
