/**
 * Kết nối sự kiện realtime `GET /api/events` (SSE).
 *
 * Mô hình nối lại:
 * - Lỗi khi `readyState === CONNECTING`: trình duyệt tự nối lại và tự gửi `Last-Event-ID` → để yên, server phát lại
 *   phần còn thiếu (hoặc `resync` nếu khoảng trống quá dài).
 * - Lỗi khi `readyState === CLOSED` (HTTP khác 200, proxy trả 502…) hoặc bấm "Thử ngay": tự tạo EventSource mới với
 *   backoff 1 → 2 → 4 → 8 → 15 giây. EventSource mới **không** gửi được header `Last-Event-ID`, nên id cuối cùng đã
 *   nhận đi theo query `?last_event_id=` (server phát lại phần thiếu, hoặc `resync` nếu khoảng trống quá dài). Chưa
 *   nhận id nào (hoặc vừa đổi project) → coi như có khoảng trống: tải lại mọi query.
 * - `server.hello` có `boot_id` khác lần trước → phát `server.restarted` và tải lại mọi query.
 */

export type EventsState = 'open' | 'reconnecting' | 'offline';

export interface ServerEvent {
  type: string;
  /** `id` SSE (= seq phía server); rỗng với sự kiện do client tự phát. */
  id: string;
  data: unknown;
}

export interface QueryInvalidator {
  invalidateQueries: () => unknown;
}

export interface ConnectEventsOptions {
  project?: number;
  onEvent: (e: ServerEvent) => void;
  onState: (s: EventsState) => void;
  /** Mỗi lần luồng lỗi (kể cả khi đang nối lại) — để kiểm tra phiên đăng nhập còn không. */
  onFailure?: () => void;
  /** Thời gian (ms) tới lần tự nối lại kế tiếp; `null` khi không có lần chờ nào. */
  onRetryIn?: (ms: number | null) => void;
  /** Dùng cho `resync`, `server.restarted` và khoảng trống sau khi tự nối lại. */
  queryClient?: QueryInvalidator;
  url?: string;
  /** Số lần thất bại liên tiếp trước khi coi là mất kết nối (mặc định 3). */
  offlineAfter?: number;
  /** Cho test thay EventSource giả. */
  EventSourceImpl?: typeof EventSource;
}

export type EventsConnection = (() => void) & {
  reconnectNow: () => void;
  /**
   * Đổi project đang theo dõi trên cùng kết nối: giữ `boot_id` (vẫn phát hiện server khởi động lại), mở lại
   * EventSource và coi như có khoảng trống (tải lại query + `resync`).
   */
  setProject: (project?: number) => void;
};

/** Các loại sự kiện server phát (SSE `event:` có tên nên phải đăng ký từng loại). */
export const EVENT_TYPES = [
  'server.hello',
  'resync',
  'job.updated',
  'job.log',
  'entity.changed',
  'llm.delta',
  'provider.health',
  'pipeline.updated',
  'queue.updated',
] as const;

export const BACKOFF_MS = [1000, 2000, 4000, 8000, 15000] as const;

const CLOSED = 2;

function parseData(raw: unknown): unknown {
  if (typeof raw !== 'string') return raw;
  if (raw === '') return {};
  try {
    return JSON.parse(raw);
  } catch {
    return raw;
  }
}

function bootIdOf(data: unknown): string | null {
  if (data && typeof data === 'object' && 'boot_id' in data) {
    const v = (data as { boot_id: unknown }).boot_id;
    return typeof v === 'string' && v ? v : null;
  }
  return null;
}

export function connectEvents(opts: ConnectEventsOptions): EventsConnection {
  const ES = opts.EventSourceImpl ?? globalThis.EventSource;
  const base = opts.url ?? '/api/events';
  let project = opts.project;
  /** id SSE cuối cùng đã nhận (để EventSource mới yêu cầu phát lại); đổi project thì bỏ. */
  let lastId: string | null = null;
  const urlFor = (withLastId: boolean) => {
    const q: string[] = [];
    if (project != null) q.push(`project=${encodeURIComponent(String(project))}`);
    if (withLastId && lastId) q.push(`last_event_id=${encodeURIComponent(lastId)}`);
    return q.length ? `${base}?${q.join('&')}` : base;
  };
  const offlineAfter = opts.offlineAfter ?? 3;

  let es: EventSource | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let stopped = false;
  let failures = 0;
  let attempt = 0; // số lần tự nối lại liên tiếp (chỉ số trong BACKOFF_MS)
  let bootId: string | null = null;
  let gap = false; // EventSource tự tạo mới: mất Last-Event-ID
  let state: EventsState | null = null;

  const setState = (s: EventsState) => {
    if (s === state) return;
    state = s;
    opts.onState(s);
  };

  const invalidateAll = () => {
    opts.queryClient?.invalidateQueries();
  };

  const handle = (ev: MessageEvent) => {
    const type = ev.type === 'message' ? 'message' : ev.type;
    const data = parseData(ev.data);
    const id = ev.lastEventId ?? '';
    if (id) lastId = id;

    if (type === 'server.hello') {
      const next = bootIdOf(data);
      if (next && bootId && next !== bootId) {
        invalidateAll();
        opts.onEvent({ type: 'server.restarted', id: '', data: { boot_id: next, previous_boot_id: bootId } });
      }
      if (next) bootId = next;
    } else if (type === 'resync') {
      invalidateAll();
    }
    opts.onEvent({ type, id, data });
  };

  const clearTimer = () => {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  };

  const markFailure = () => {
    failures += 1;
    opts.onFailure?.();
    setState(failures >= offlineAfter ? 'offline' : 'reconnecting');
  };

  const scheduleReconnect = () => {
    clearTimer();
    const delay = BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)];
    attempt += 1;
    opts.onRetryIn?.(delay);
    timer = setTimeout(() => {
      timer = null;
      open(true);
    }, delay);
  };

  const close = () => {
    if (!es) return;
    es.onopen = null;
    es.onerror = null;
    es.onmessage = null;
    for (const t of EVENT_TYPES) es.removeEventListener(t, handle as EventListener);
    es.close();
    es = null;
  };

  function open(fresh: boolean) {
    if (stopped) return;
    close();
    if (typeof ES !== 'function') {
      // Môi trường không có EventSource (test, trình duyệt rất cũ): không realtime, không ném lỗi.
      setState('offline');
      return;
    }
    // EventSource mới: gửi id cuối qua query để server phát lại; chưa có id → khoảng trống.
    const resume = fresh && lastId != null;
    gap = gap || (fresh && !resume);
    const source = new ES(urlFor(resume), { withCredentials: true });
    es = source;
    source.onopen = () => {
      failures = 0;
      attempt = 0;
      opts.onRetryIn?.(null);
      setState('open');
      if (gap) {
        // EventSource mới không gửi được Last-Event-ID: có thể đã lỡ sự kiện → xử lý như sự kiện resync.
        gap = false;
        invalidateAll();
        opts.onEvent({ type: 'resync', id: '', data: { reason: 'reconnect' } });
      }
    };
    source.onerror = () => {
      if (stopped || es !== source) return;
      markFailure();
      if (source.readyState === CLOSED) {
        close();
        scheduleReconnect();
      }
      // CONNECTING: trình duyệt tự nối lại và gửi Last-Event-ID.
    };
    source.onmessage = handle;
    for (const t of EVENT_TYPES) source.addEventListener(t, handle as EventListener);
  }

  open(false);

  const stop = () => {
    stopped = true;
    clearTimer();
    close();
  };

  return Object.assign(stop, {
    reconnectNow: () => {
      if (stopped) return;
      clearTimer();
      opts.onRetryIn?.(null);
      open(true);
    },
    setProject: (next?: number) => {
      if (stopped || next === project) return;
      project = next;
      lastId = null; // luồng khác: không phát lại theo id cũ, tải lại toàn bộ
      gap = true;
      clearTimer();
      attempt = 0;
      opts.onRetryIn?.(null);
      open(true);
    },
  });
}
