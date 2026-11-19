import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { connectEvents, type EventsState, type ServerEvent } from './events';

/** EventSource giả: test tự bắn open / error / message; không có mạng. */
class FakeEventSource {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  static instances: FakeEventSource[] = [];

  readonly url: string;
  readyState = FakeEventSource.CONNECTING;
  onopen: ((ev: Event) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  private listeners = new Map<string, Set<(ev: MessageEvent) => void>>();

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, fn: (ev: MessageEvent) => void) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type)!.add(fn);
  }

  removeEventListener(type: string, fn: (ev: MessageEvent) => void) {
    this.listeners.get(type)?.delete(fn);
  }

  close() {
    this.readyState = FakeEventSource.CLOSED;
  }

  // --- điều khiển từ test ---
  open() {
    this.readyState = FakeEventSource.OPEN;
    this.onopen?.(new Event('open'));
  }

  /** Lỗi mà trình duyệt sẽ tự nối lại (giữ Last-Event-ID). */
  dropAndRetry() {
    this.readyState = FakeEventSource.CONNECTING;
    this.onerror?.(new Event('error'));
  }

  /** Lỗi chết hẳn (HTTP khác 200, sai content-type…): trình duyệt không tự nối lại. */
  fail() {
    this.readyState = FakeEventSource.CLOSED;
    this.onerror?.(new Event('error'));
  }

  emit(type: string, data: unknown, id = '') {
    const ev = new MessageEvent(type, { data: JSON.stringify(data), lastEventId: id });
    if (type === 'message') this.onmessage?.(ev);
    this.listeners.get(type)?.forEach((fn) => fn(ev));
  }
}

const last = () => FakeEventSource.instances[FakeEventSource.instances.length - 1];

function setup(extra: Partial<Parameters<typeof connectEvents>[0]> = {}) {
  const events: ServerEvent[] = [];
  const states: EventsState[] = [];
  const invalidateQueries = vi.fn();
  const stop = connectEvents({
    onEvent: (e) => events.push(e),
    onState: (s) => states.push(s),
    queryClient: { invalidateQueries },
    EventSourceImpl: FakeEventSource as unknown as typeof EventSource,
    ...extra,
  });
  return { events, states, invalidateQueries, stop };
}

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('connectEvents', () => {
  it('mở /api/events (kèm project) và báo trạng thái open', () => {
    const { states, stop } = setup({ project: 7 });
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(last().url).toBe('/api/events?project=7');
    last().open();
    expect(states).toEqual(['open']);
    stop();
  });

  it('không có project thì không thêm query', () => {
    const { stop } = setup();
    expect(last().url).toBe('/api/events');
    stop();
  });

  it('chuyển sự kiện có tên kèm data JSON và id', () => {
    const { events, stop } = setup();
    last().open();
    last().emit('job.updated', { id: 4, status: 'running' }, '12');
    expect(events).toContainEqual({ type: 'job.updated', id: '12', data: { id: 4, status: 'running' } });
    stop();
  });

  it('lỗi chết hẳn → tự nối lại với backoff tăng dần 1→2→4→8→15→15 giây', () => {
    const { stop } = setup();
    const delays = [1000, 2000, 4000, 8000, 15000, 15000];
    for (const d of delays) {
      const before = FakeEventSource.instances.length;
      last().fail();
      vi.advanceTimersByTime(d - 1);
      expect(FakeEventSource.instances.length).toBe(before);
      vi.advanceTimersByTime(1);
      expect(FakeEventSource.instances.length).toBe(before + 1);
    }
    stop();
  });

  it('báo reconnecting rồi offline sau nhiều lần thất bại liên tiếp', () => {
    const { states, stop } = setup();
    last().fail();
    expect(states.at(-1)).toBe('reconnecting');
    vi.advanceTimersByTime(1000);
    last().fail();
    vi.advanceTimersByTime(2000);
    last().fail();
    expect(states.at(-1)).toBe('offline');
    stop();
  });

  it('mở lại thành công thì backoff về 1 giây', () => {
    const { states, stop } = setup();
    last().fail();
    vi.advanceTimersByTime(1000);
    last().fail();
    vi.advanceTimersByTime(2000);
    last().open();
    expect(states.at(-1)).toBe('open');
    const before = FakeEventSource.instances.length;
    last().fail();
    vi.advanceTimersByTime(1000);
    expect(FakeEventSource.instances.length).toBe(before + 1);
    stop();
  });

  it('lỗi mà trình duyệt tự nối lại (CONNECTING) → không tạo EventSource mới, giữ Last-Event-ID', () => {
    const { states, stop } = setup();
    last().open();
    last().dropAndRetry();
    vi.advanceTimersByTime(60_000);
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(states.at(-1)).toBe('reconnecting');
    last().open();
    expect(states.at(-1)).toBe('open');
    stop();
  });

  it('báo thời gian chờ trước lần thử kế tiếp', () => {
    const onRetryIn = vi.fn();
    const { stop } = setup({ onRetryIn });
    last().fail();
    expect(onRetryIn).toHaveBeenLastCalledWith(1000);
    vi.advanceTimersByTime(1000);
    last().open();
    expect(onRetryIn).toHaveBeenLastCalledWith(null);
    stop();
  });

  it('server.hello cùng boot_id không phát server.restarted; boot_id khác thì phát và tải lại mọi query', () => {
    const { events, invalidateQueries, stop } = setup();
    last().open();
    last().emit('server.hello', { boot_id: 'a' }, '1');
    last().dropAndRetry();
    last().open();
    last().emit('server.hello', { boot_id: 'a' }, '2');
    expect(events.filter((e) => e.type === 'server.restarted')).toHaveLength(0);
    expect(invalidateQueries).not.toHaveBeenCalled();

    last().dropAndRetry();
    last().open();
    last().emit('server.hello', { boot_id: 'b' }, '1');
    const restarted = events.filter((e) => e.type === 'server.restarted');
    expect(restarted).toHaveLength(1);
    expect(restarted[0].data).toMatchObject({ boot_id: 'b', previous_boot_id: 'a' });
    expect(invalidateQueries).toHaveBeenCalledTimes(1);
    stop();
  });

  it('resync → tải lại mọi query và vẫn chuyển sự kiện', () => {
    const { events, invalidateQueries, stop } = setup();
    last().open();
    last().emit('resync', {}, '40');
    expect(invalidateQueries).toHaveBeenCalledTimes(1);
    expect(events.map((e) => e.type)).toContain('resync');
    stop();
  });

  it('tự tạo kết nối mới (mất Last-Event-ID) → coi như có khoảng trống, tải lại mọi query khi mở', () => {
    const { events, invalidateQueries, stop } = setup();
    last().open();
    expect(invalidateQueries).not.toHaveBeenCalled();
    last().fail();
    vi.advanceTimersByTime(1000);
    last().open();
    expect(invalidateQueries).toHaveBeenCalledTimes(1);
    // dữ liệu dựng từ sự kiện (thanh lô) cũng phải biết là có khoảng trống
    expect(events.filter((e) => e.type === 'resync')).toEqual([{ type: 'resync', id: '', data: { reason: 'reconnect' } }]);
    stop();
  });

  it('reconnectNow nối lại ngay, không chờ backoff', () => {
    const { stop } = setup();
    last().fail();
    const before = FakeEventSource.instances.length;
    stop.reconnectNow();
    expect(FakeEventSource.instances.length).toBe(before + 1);
    vi.advanceTimersByTime(60_000);
    expect(FakeEventSource.instances.length).toBe(before + 1);
    stop();
  });

  it('dừng thì đóng kết nối và không nối lại nữa', () => {
    const { stop } = setup();
    const es = last();
    es.fail();
    stop();
    vi.advanceTimersByTime(60_000);
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(es.readyState).toBe(FakeEventSource.CLOSED);
  });

  it('data không phải JSON vẫn chuyển đi (dạng chuỗi), không ném lỗi', () => {
    const { events, stop } = setup();
    last().open();
    const ev = new MessageEvent('job.log', { data: 'không phải json', lastEventId: '3' });
    // gọi listener trực tiếp như trình duyệt
    (last() as unknown as { listeners: Map<string, Set<(e: MessageEvent) => void>> }).listeners
      .get('job.log')!
      .forEach((fn) => fn(ev));
    expect(events).toContainEqual({ type: 'job.log', id: '3', data: 'không phải json' });
    stop();
  });
});
