/** Chữ hiển thị cho job (DESIGN §7–8): nhãn trạng thái, loại, lane, lỗi — không bao giờ lộ câu tiếng Anh thô. */
import type { Connection, Job } from '../api/types';
import type { PillStatus } from '../components/ui/StatusPill';
import { parseServerTime } from './time';

export interface JobStatusView {
  pill: PillStatus;
  label: string;
}

/** Trạng thái job → pill (icon + màu) + nhãn tiếng Việt. */
export function jobStatusView(j: Pick<Job, 'status' | 'progress'>): JobStatusView {
  switch (j.status) {
    case 'queued':
      return { pill: 'queued', label: 'Chờ' };
    case 'running':
      return { pill: 'running', label: j.progress != null && j.progress > 0 ? `Đang chạy ${Math.floor(j.progress)}%` : 'Đang chạy' };
    case 'succeeded':
      return { pill: 'done', label: 'Xong' };
    case 'failed':
      return { pill: 'failed', label: 'Lỗi' };
    case 'cancelled':
      return { pill: 'done', label: 'Đã huỷ' };
    case 'interrupted':
      return { pill: 'failed', label: 'Bị gián đoạn' };
    case 'needs_attention':
      return { pill: 'warn', label: 'Cần xử lý' };
    case 'paused':
      return { pill: 'queued', label: 'Tạm dừng' };
    case 'waiting_review':
      return { pill: 'queued', label: 'Chờ duyệt' };
    case 'waiting_input':
      return { pill: 'queued', label: 'Chờ nhập' };
    default:
      return { pill: 'queued', label: j.status };
  }
}

/** Bộ lọc trạng thái trên trang Việc (thứ tự hiển thị). */
export const STATUS_FILTERS: { value: string; label: string }[] = [
  { value: '', label: 'Tất cả' },
  { value: 'running', label: 'Đang chạy' },
  { value: 'queued', label: 'Chờ' },
  { value: 'needs_attention', label: 'Cần xử lý' },
  { value: 'failed', label: 'Lỗi' },
  { value: 'interrupted', label: 'Bị gián đoạn' },
  { value: 'succeeded', label: 'Xong' },
  { value: 'cancelled', label: 'Đã huỷ' },
];

const TYPE_LABEL: Record<string, string> = {
  'provider.try_image': 'Thử tạo ảnh',
  'provider.try_video': 'Thử tạo video',
  'provider.try_tts': 'Thử đọc lời',
  'provider.try_llm': 'Thử LLM',
  'provider.try_stt': 'Thử nhận dạng lời',
};

/** Loại job → nhãn; loại chưa biết giữ nguyên tên (là ID kỹ thuật, hiển thị mono). */
export function jobTypeLabel(type: string): string {
  return TYPE_LABEL[type] ?? type;
}

export function knownJobTypes(): string[] {
  return Object.keys(TYPE_LABEL);
}

/** Lane `conn:<id>` → tên kết nối; lane khác (`ffmpeg`, `cpu`…) giữ nguyên. */
export function laneLabel(lane: string, connections: Pick<Connection, 'id' | 'name'>[] = []): string {
  const m = /^conn:(\d+)$/.exec(lane);
  if (m) {
    const c = connections.find((x) => x.id === Number(m[1]));
    return c ? c.name : `Kết nối #${m[1]}`;
  }
  return lane;
}

/**
 * Câu lỗi cho người dùng. `error_message` từ `AppError` của backend là tiếng Việt; riêng lỗi không lường trước
 * (`internal_error`) chứa tên exception Python (tiếng Anh) → thay bằng câu cố định, chi tiết nằm trong log.
 */
const ERROR_TEXT: Record<string, string> = {
  internal_error: 'Lỗi nội bộ không lường trước.',
  workdir_unavailable: 'Không tạo được thư mục làm việc của job.',
};

export function jobErrorText(j: Pick<Job, 'error_code' | 'error_message'>): string | null {
  if (!j.error_code && !j.error_message) return null;
  if (j.error_code && ERROR_TEXT[j.error_code]) return ERROR_TEXT[j.error_code];
  return j.error_message || `Lỗi (${j.error_code}).`;
}

/** Số giây còn lại theo `eta_at`; không có → null. */
export function jobEtaSeconds(j: Pick<Job, 'eta_at'>, now: number = Date.now()): number | null {
  const t = parseServerTime(j.eta_at);
  return t == null ? null : Math.max(0, (t - now) / 1000);
}

/** Tên ngắn của job cho hàng/tiêu đề: "Thử tạo ảnh · #12". */
export function jobTitle(j: Pick<Job, 'id' | 'type'>): string {
  return `${jobTypeLabel(j.type)} · #${j.id}`;
}

/** Job đã gửi tới provider (có id phía provider) → chạy lại là tạo job trả phí mới. */
export function hasProviderId(j: Pick<Job, 'external_id'>): boolean {
  return !!j.external_id;
}

export const CANCELLABLE = new Set(['queued', 'running', 'paused', 'waiting_review', 'waiting_input']);
export const RETRYABLE = new Set(['succeeded', 'failed', 'cancelled', 'interrupted']);
