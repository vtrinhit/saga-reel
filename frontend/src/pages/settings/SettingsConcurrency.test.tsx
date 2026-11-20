import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { api } from '../../api/client';
import { CONCURRENCY_REFRESH_MS } from '../../api/hooks';
import { emitServerEvent } from '../../lib/eventHub';
import { summarizeUsage, usageText, type ConcurrencyOverview } from '../../lib/concurrency';
import { useToasts } from '../../stores/toast';
import { ConcurrencySummary } from '../jobs/ConcurrencySummary';
import { APPLIED_MESSAGE, COMMIT_DELAY_MS, SettingsConcurrency } from './SettingsConcurrency';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const originalAdapter = api.defaults.adapter;
let container: HTMLDivElement;
let root: Root;
let qc: QueryClient;
let calls: { op: string; body: unknown }[];
let overview: ConcurrencyOverview;

function makeOverview(): ConcurrencyOverview {
  const kind = (k: string, label: string, cap: number, running = 0, queued = 0, connections: ConcurrencyOverview['kinds'][number]['connections'] = []) =>
    ({ kind: k, label, key: `concurrency.${k}`, cap, running, queued, connections }) as ConcurrencyOverview['kinds'][number];
  return {
    min: 1,
    max: 20,
    paused: false,
    kinds: [
      kind('image', 'Ảnh', 2),
      kind('video', 'Video', 2, 2, 3, [
        { lane: 'conn:4', connection_id: 4, name: 'GrokTele', enabled: true, cap: 3, running: 2, queued: 3 },
        { lane: 'conn:5', connection_id: 5, name: 'MaiVideo', enabled: false, cap: 1, running: 0, queued: 0 },
      ]),
      kind('llm', 'LLM', 3, 0, 0, [{ lane: 'story', connection_id: null, name: 'Viết truyện / dịch lồng tiếng', enabled: true, cap: 2, running: 0, queued: 0 }]),
      kind('tts', 'Giọng đọc', 2),
      kind('stt', 'Nhận dạng giọng', 1),
      kind('render', 'Dựng phim', 1),
    ],
  };
}

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  calls = [];
  overview = makeOverview();
  useToasts.getState().clear();
  api.defaults.adapter = async (config) => {
    const op = String(config.url).split('/').pop()!;
    const body = config.data ? JSON.parse(String(config.data)) : {};
    calls.push({ op, body });
    if (op === 'queue.set_concurrency') {
      const values = (body as { values: Record<string, number> }).values;
      overview = { ...overview, kinds: overview.kinds.map((k) => (values[k.kind] != null ? { ...k, cap: values[k.kind] } : k)) };
    }
    return { data: structuredClone(overview), status: 200, statusText: 'OK', headers: {}, config, request: {} };
  };
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  api.defaults.adapter = originalAdapter;
});

const wait = (ms: number) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
/** Chờ tới khi trang đã có dữ liệu (máy chạy cả bộ test có thể chậm). */
async function loaded() {
  for (let i = 0; i < 200 && !document.getElementById('cc-video'); i++) await wait(10);
}
const click = (el: Element) => act(() => (el as HTMLElement).click());
const byLabel = (label: string) => document.querySelector(`[aria-label="${label}"]`) as HTMLButtonElement;

function render(ui: React.ReactNode) {
  act(() =>
    root.render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>{ui}</MemoryRouter>
      </QueryClientProvider>,
    ),
  );
}

describe('Cài đặt › Luồng', () => {
  it('hiện trần, số đang chạy/chờ và trần riêng của từng kết nối', async () => {
    render(<SettingsConcurrency />);
    await loaded();
    const text = container.textContent ?? '';
    for (const label of ['Ảnh', 'Video', 'LLM', 'Giọng đọc', 'Nhận dạng giọng', 'Dựng phim']) expect(text).toContain(label);
    expect(text).toContain('2/2 đang chạy, 3 chờ');
    expect(text).toContain('GrokTele');
    expect(text).toContain('trần riêng 3');
    expect(text).toContain('(đang tắt)');
    expect((document.getElementById('cc-video') as HTMLInputElement).value).toBe('2');
    expect(byLabel('Video: 2 trên 2 luồng đang chạy')).not.toBeNull();
    expect(container.querySelector('a[aria-label="Sửa trần riêng của kết nối GrokTele"]')?.getAttribute('href')).toBe('/settings/providers');
  });

  it('bấm + liên tiếp: hiện ngay, gộp thành một lần ghi, báo đã áp dụng', async () => {
    render(<SettingsConcurrency />);
    await loaded();
    click(byLabel('Thêm một luồng Video'));
    click(byLabel('Thêm một luồng Video'));
    expect((document.getElementById('cc-video') as HTMLInputElement).value).toBe('4'); // optimistic
    expect(calls.filter((c) => c.op === 'queue.set_concurrency')).toHaveLength(0);
    await wait(COMMIT_DELAY_MS + 50);
    const sets = calls.filter((c) => c.op === 'queue.set_concurrency');
    expect(sets).toEqual([{ op: 'queue.set_concurrency', body: { values: { video: 4 } } }]);
    expect(useToasts.getState().toasts.map((t) => t.message)).toContain(APPLIED_MESSAGE);
    expect((document.getElementById('cc-video') as HTMLInputElement).value).toBe('4');
  });

  it('rời trang trước khi hết thời gian gộp: vẫn ghi thay đổi', async () => {
    render(<SettingsConcurrency />);
    await loaded();
    click(byLabel('Thêm một luồng Ảnh'));
    act(() => root.render(<QueryClientProvider client={qc}><MemoryRouter><p>khác</p></MemoryRouter></QueryClientProvider>));
    await wait(20);
    expect(calls.filter((c) => c.op === 'queue.set_concurrency').map((c) => c.body)).toEqual([{ values: { image: 3 } }]);
  });

  it('nút bớt bị khoá ở trần 1; hạ trần dưới số đang chạy thì báo việc làm nốt', async () => {
    render(<SettingsConcurrency />);
    await loaded();
    expect(byLabel('Bớt một luồng Dựng phim').disabled).toBe(true);
    click(byLabel('Bớt một luồng Video'));
    expect(container.textContent).toContain('Đang chạy nhiều hơn trần mới');
    await wait(COMMIT_DELAY_MS + 50);
    expect(calls.filter((c) => c.op === 'queue.set_concurrency').at(-1)?.body).toEqual({ values: { video: 1 } });
  });

  it('nhận sự kiện queue.updated / job.updated thì đọc lại số luồng', async () => {
    render(<SettingsConcurrency />);
    await loaded();
    const reads = () => calls.filter((c) => c.op === 'queue.concurrency').length;
    const before = reads();
    overview = { ...overview, kinds: overview.kinds.map((k) => (k.kind === 'image' ? { ...k, cap: 7 } : k)) };
    act(() => {
      emitServerEvent({ type: 'queue.updated', id: '1', data: { reason: 'concurrency' } });
      emitServerEvent({ type: 'job.updated', id: '2', data: {} });
    });
    await wait(CONCURRENCY_REFRESH_MS + 100);
    expect(reads()).toBe(before + 1); // gộp: hai sự kiện → một lần đọc
    expect((document.getElementById('cc-image') as HTMLInputElement).value).toBe('7');
  });

  it('đầu trang Việc tóm tắt loại đang bận và dẫn sang Cài đặt › Luồng', async () => {
    render(<ConcurrencySummary />);
    for (let i = 0; i < 200 && !container.querySelector('a'); i++) await wait(10);
    const link = container.querySelector('a[href="/settings/concurrency"]');
    expect(link?.textContent).toContain('Video 2/2');
    expect(link?.textContent).toContain('+3 chờ');
  });
});

describe('lib/concurrency', () => {
  it('usageText và summarizeUsage', () => {
    expect(usageText({ running: 0, cap: 2, queued: 0 })).toBe('Rảnh');
    expect(usageText({ running: 1, cap: 2, queued: 0 })).toBe('1/2 đang chạy');
    const s = summarizeUsage(makeOverview());
    expect(s.running).toBe(2);
    expect(s.cap).toBe(11);
    expect(s.busy.map((b) => b.kind)).toEqual(['video']);
  });
});
