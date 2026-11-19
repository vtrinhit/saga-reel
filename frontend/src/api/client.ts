/**
 * HTTP client dùng chung: axios cùng origin, gửi cookie, gắn CSRF, đổi mọi lỗi thành `ApiError`.
 *
 * Backend trả lỗi dạng `{"ok": false, "error_code", "detail", "hint", "status", "extra"}`. UI chỉ hiển thị
 * `detail`/`hint` (tiếng Việt); không bao giờ hiển thị `extra.errors[].msg` (tiếng Anh của Pydantic).
 */
import axios, { AxiosError, AxiosHeaders, type AxiosInstance } from 'axios';

export const CSRF_COOKIE = 'sr_csrf';
export const CSRF_HEADER = 'X-SR-CSRF';

export class ApiError extends Error {
  readonly code: string;
  readonly detail: string;
  readonly hint: string | null;
  readonly status: number;
  /** Giây chờ theo header `Retry-After` (chỉ khi server gửi). */
  readonly retryAfter: number | null;
  /** Dữ liệu phụ cho code (vd `extra.errors[].loc` để tô đỏ trường) — không hiển thị nguyên văn. */
  readonly extra: Record<string, unknown>;

  constructor(init: {
    code: string;
    detail: string;
    hint?: string | null;
    status: number;
    retryAfter?: number | null;
    extra?: Record<string, unknown>;
  }) {
    super(init.detail);
    this.name = 'ApiError';
    this.code = init.code;
    this.detail = init.detail;
    this.hint = init.hint ?? null;
    this.status = init.status;
    this.retryAfter = init.retryAfter ?? null;
    this.extra = init.extra ?? {};
  }
}

export function readCookie(name: string): string | null {
  if (typeof document === 'undefined') return null;
  for (const part of document.cookie.split(';')) {
    const i = part.indexOf('=');
    if (i < 0) continue;
    if (part.slice(0, i).trim() === name) {
      const value = part.slice(i + 1).trim();
      try {
        return decodeURIComponent(value);
      } catch {
        return value;
      }
    }
  }
  return null;
}

/** Điều hướng tách riêng để test thay được (jsdom không điều hướng thật). */
export const nav = {
  toLogin(url: string): void {
    window.location.assign(url);
  },
};

export const LOGIN_PATH = '/login';

/** URL trang đăng nhập kèm `next` = trang đang xem (không phải URL của API). */
export function loginUrl(loc: Pick<Location, 'pathname' | 'search' | 'hash'> = window.location): string {
  const next = `${loc.pathname}${loc.search}${loc.hash}`;
  if (!next || next === '/' ) return LOGIN_PATH;
  return `${LOGIN_PATH}?next=${encodeURIComponent(next)}`;
}

function parseRetryAfter(value: unknown): number | null {
  if (value == null) return null;
  const s = String(value).trim();
  if (/^\d+$/.test(s)) return Number(s);
  const at = Date.parse(s);
  if (Number.isNaN(at)) return null;
  return Math.max(0, Math.ceil((at - Date.now()) / 1000));
}

interface ErrorBody {
  error_code?: unknown;
  detail?: unknown;
  hint?: unknown;
  extra?: unknown;
}

function isErrorBody(data: unknown): data is ErrorBody {
  return !!data && typeof data === 'object' && typeof (data as ErrorBody).error_code === 'string';
}

/** Đổi lỗi bất kỳ (axios, mạng, code) thành ApiError có câu tiếng Việt. */
export function toApiError(err: unknown): ApiError {
  if (err instanceof ApiError) return err;
  if (err instanceof AxiosError) {
    const res = err.response;
    if (!res) {
      if (err.code === AxiosError.ERR_CANCELED) {
        return new ApiError({ code: 'cancelled', detail: 'Yêu cầu đã bị huỷ.', status: 0 });
      }
      if (err.code === AxiosError.ECONNABORTED || err.code === AxiosError.ETIMEDOUT) {
        return new ApiError({
          code: 'timeout',
          detail: 'Máy chủ không trả lời kịp.',
          hint: 'Thử lại sau ít phút.',
          status: 0,
        });
      }
      return new ApiError({
        code: 'network_error',
        detail: 'Không kết nối được máy chủ.',
        hint: 'Kiểm tra máy nhà còn bật và SagaReel Studio còn chạy.',
        status: 0,
      });
    }
    const retryAfter = parseRetryAfter(
      res.headers instanceof AxiosHeaders ? res.headers.get('retry-after') : (res.headers as Record<string, unknown>)?.['retry-after'],
    );
    if (isErrorBody(res.data)) {
      const body = res.data;
      return new ApiError({
        code: body.error_code as string,
        detail: typeof body.detail === 'string' && body.detail ? body.detail : `Máy chủ trả lỗi ${res.status}.`,
        hint: typeof body.hint === 'string' ? body.hint : null,
        status: res.status,
        retryAfter,
        extra: body.extra && typeof body.extra === 'object' ? (body.extra as Record<string, unknown>) : {},
      });
    }
    return new ApiError({
      code: `http_${res.status}`,
      detail: `Máy chủ trả lỗi ${res.status}.`,
      hint: res.status >= 500 ? 'Xem log của máy chủ để biết chi tiết.' : null,
      status: res.status,
      retryAfter,
    });
  }
  const message = err instanceof Error ? err.message : String(err);
  return new ApiError({ code: 'client_error', detail: `Lỗi phía trình duyệt: ${message}`, status: 0 });
}

export const api: AxiosInstance = axios.create({
  baseURL: '',
  withCredentials: true,
  headers: { Accept: 'application/json' },
});

api.interceptors.request.use((config) => {
  const token = readCookie(CSRF_COOKIE);
  if (token) config.headers.set(CSRF_HEADER, token);
  return config;
});

api.interceptors.response.use(
  (res) => res,
  (err: unknown) => {
    const e = toApiError(err);
    if (e.status === 401 && e.code === 'auth_required' && window.location.pathname !== LOGIN_PATH) {
      nav.toLogin(loginUrl());
    }
    return Promise.reject(e);
  },
);
