/** Chữ hiển thị cho job (DESIGN §7–8): nhãn trạng thái, loại, lane, lỗi — không bao giờ lộ câu tiếng Anh thô. */
import type { Connection, Job } from '../api/types';
import type { PillStatus } from '../components/ui/StatusPill';
import { parseServerTime } from './time';

export interface JobStatusView {
  pill: PillStatus;
  label: string;
}

/** `job.list` / `job.get` kèm vị trí trong hàng đợi của lane (1 = chạy kế tiếp; null khi không chờ). */
export type WithQueuePosition = { queue_position?: number | null };

/** Trạng thái job → pill (icon + màu) + nhãn tiếng Việt; việc chờ có vị trí hàng đợi → "Chờ #n". */
export function jobStatusView(j: Pick<Job, 'status' | 'progress'> & WithQueuePosition): JobStatusView {
  switch (j.status) {
    case 'queued':
      return { pill: 'queued', label: j.queue_position ? `Chờ #${j.queue_position}` : 'Chờ' };
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
 * Câu lỗi cho người dùng theo `error_code` (mã tiếng Anh ổn định). Chỉ khi mã chưa có trong bảng mới dùng
 * `error_message` của server (AppError — tiếng Việt). Chi tiết thô (tên exception, URL…) nằm trong log; `hint` của
 * server hiện riêng bên dưới.
 */
export const ERROR_TEXT: Record<string, string> = {
  internal_error: 'Lỗi nội bộ không lường trước.',
  workdir_unavailable: 'Không tạo được thư mục làm việc của việc này.',
  provider_unavailable: 'Provider không dùng được (kết nối đang tắt, chưa cấu hình hoặc không phản hồi).',
  provider_rejected: 'Provider từ chối yêu cầu.',
  budget_exceeded: 'Vượt ngân sách.',
  capability_mismatch: 'Provider không hỗ trợ yêu cầu này.',
  job_unknown: 'Không rõ provider đã nhận việc này hay chưa.',
  maivideo_job_unknown: 'Không rõ MaiVideo đã nhận việc này hay chưa.',
  remote_cancelled: 'Việc đã bị huỷ ở phía provider.',
  retries_exhausted: 'Đã thử lại hết số lần cho phép.',
  interrupted: 'Việc bị gián đoạn (app tắt khi đang chạy).',
  already_succeeded: 'Việc đã xong; chạy lại thường chỉ lấy lại đúng kết quả cũ.',
  params_patch_requires_new_job: 'Provider đã nhận việc này — sửa tham số cần tạo việc trả phí mới.',
  params_patch_invalid: 'Chỉ sửa được tham số việc đã có; muốn đổi kết nối thì tạo việc mới.',
  cleared_by_user: 'Bạn đã cho việc này thất bại.',
};

/** Mã lỗi mà `detail` của server là chi tiết kỹ thuật thô (tên exception tiếng Anh) — không bao giờ hiện. */
const RAW_DETAIL_CODES = new Set(['internal_error']);

export function errorCodeText(code: string | null | undefined): string | null {
  return code ? (ERROR_TEXT[code] ?? null) : null;
}

export function jobErrorText(j: Pick<Job, 'error_code' | 'error_message'>): string | null {
  if (!j.error_code && !j.error_message) return null;
  return errorCodeText(j.error_code) ?? (j.error_message || `Lỗi (${j.error_code}).`);
}

/** Các dòng hiển thị một lỗi: câu chính (theo mã), chi tiết của server (giữ số liệu, vd "60/60"), gợi ý của server. */
export interface ErrorLines {
  text: string | null;
  detail: string | null;
  hint: string | null;
}

/**
 * Lỗi bất kỳ (`ApiError` hoặc lỗi job) → dòng hiển thị. Mã đã có câu tiếng Việt thì câu đó là dòng chính và `detail`
 * của server vẫn hiện bên dưới (trừ chi tiết thô kiểu `internal_error`); mã chưa có thì `detail` là dòng chính.
 * `hint` của server luôn hiện.
 */
export function errorLines(e: { code?: string | null; detail?: string | null; hint?: string | null }): ErrorLines | null {
  const code = e.code || null;
  const detail = e.detail?.trim() || null;
  const hint = e.hint?.trim() || null;
  if (!code && !detail && !hint) return null;
  const mapped = errorCodeText(code);
  const text = mapped ?? detail ?? (code ? `Lỗi (${code}).` : null);
  const extra = mapped && detail && detail !== mapped && !(code && RAW_DETAIL_CODES.has(code)) ? detail : null;
  return { text, detail: extra, hint };
}

export function jobErrorLines(j: Pick<Job, 'error_code' | 'error_message' | 'hint'>): ErrorLines | null {
  return errorLines({ code: j.error_code, detail: j.error_message, hint: j.hint });
}

/** Công đoạn (`stage`) chỉ đúng khi việc đang chạy; việc đã xong/lỗi/chờ không hiện công đoạn cũ. */
export function jobStageText(j: Pick<Job, 'status' | 'stage'>): string | null {
  return j.status === 'running' && j.stage ? j.stage : null;
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

export const CANCELLABLE = new Set(['queued', 'running', 'paused', 'waiting_review', 'waiting_input']);
export const RETRYABLE = new Set(['succeeded', 'failed', 'cancelled', 'interrupted']);

/** Hành động chạy lại/xử lý một việc được phép — theo đúng luật của backend (`services/jobs.py`, docs/providers.md). */
export interface JobActions {
  /** "Xử lý" (`job.clear_attention`: gửi lại / cho thất bại): việc `needs_attention` hoặc `needs_decision`. */
  attention: boolean;
  /** Mở bước chạy lại (`job.retry`). */
  retry: boolean;
  /** Chạy lại thường (không `force_new`): backend từ chối việc đã xong mà provider giữ kết quả (`already_succeeded`). */
  plainRetry: boolean;
  /** Provider giữ việc (`provider_holds`) → có lựa chọn "Tạo việc trả phí mới" (`force_new`). */
  forceNew: boolean;
  /**
   * Bước "Xử lý" của việc chỉ mang dấu `submitting` và còn `client_ref` đã gửi: gửi lại đúng ref cũ, provider có thể
   * trả lại việc đã làm bằng prompt cũ → không cho sửa prompt.
   */
  lockPatch: boolean;
}

export function jobActions(
  j: Pick<Job, 'status' | 'provider_holds' | 'needs_decision' | 'external_id' | 'external_state'>,
): JobActions {
  const attention = j.status === 'needs_attention' || !!j.needs_decision;
  const retry = !attention && RETRYABLE.has(j.status);
  const holds = !!j.provider_holds;
  const state = j.external_state ?? {};
  const markerOnly = !j.external_id && state.state === 'submitting';
  const storedRef = typeof state.client_ref === 'string' && state.client_ref !== '';
  return {
    attention,
    retry,
    plainRetry: retry && !(j.status === 'succeeded' && holds),
    forceNew: retry && holds,
    lockPatch: attention && markerOnly && storedRef,
  };
}

/** Nhãn nút hành động chính trên hàng/drawer; null = không có. */
export function jobActionLabel(a: JobActions): string | null {
  if (a.attention) return 'Xử lý';
  if (a.retry) return a.plainRetry ? 'Thử lại' : 'Tạo việc trả phí mới';
  return null;
}
