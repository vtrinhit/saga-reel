import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { AxiosAdapter } from 'axios';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { api } from '../api/client';
import { safeNext } from '../pages/Login';
import { routes } from './routes';

// React 19: báo cho act() biết đang chạy trong môi trường test.
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const originalAdapter = api.defaults.adapter;
let container: HTMLDivElement;
let root: Root;

function reply(status: number, data: unknown): AxiosAdapter {
  return async (config) => {
    if (status >= 400) {
      const { AxiosError } = await import('axios');
      throw new AxiosError('x', 'ERR_BAD_RESPONSE', config, {}, { data, status, statusText: '', headers: {}, config });
    }
    return { data, status, statusText: 'OK', headers: {}, config, request: {} };
  };
}

async function render(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(
      <QueryClientProvider client={qc}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );
  });
  // để query auth/status xong
  await act(async () => {
    await new Promise((r) => setTimeout(r, 20));
  });
  return router;
}

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  api.defaults.adapter = originalAdapter;
});

describe('shell (smoke)', () => {
  it('trang Projects hiện trạng thái trống khi đã đăng nhập (máy nhà)', async () => {
    api.defaults.adapter = reply(200, {
      authenticated: true,
      role: 'owner',
      via: 'local',
      local: true,
      password_set: { owner: false, viewer: false },
    });
    await render('/projects');
    expect(container.textContent).toContain('Chưa có project');
    expect(container.textContent).toContain('Máy nhà');
  });

  it('chưa đăng nhập → chuyển về /login?next=…', async () => {
    api.defaults.adapter = reply(200, {
      authenticated: false,
      role: null,
      via: null,
      local: false,
      password_set: { owner: true, viewer: false },
    });
    const router = await render('/settings/providers');
    expect(router.state.location.pathname).toBe('/login');
    expect(router.state.location.search).toBe('?next=%2Fsettings%2Fproviders');
    expect(container.querySelector('input[type="password"]')).not.toBeNull();
    expect(container.textContent).toContain('Nhớ thiết bị này (30 ngày)');
  });

  it('backend chưa có /api/auth (404) → shell vẫn dùng được (trang Việc báo lỗi tải, không trắng)', async () => {
    api.defaults.adapter = reply(404, { detail: 'Not Found' });
    await render('/jobs');
    expect(container.querySelector('h1')?.textContent).toBe('Việc');
    expect(container.textContent).toContain('Không tải được danh sách việc');
  });

  it('trang Việc và Cài đặt dựng được từ dữ liệu thật của op (job, kết nối, LLM chưa có)', async () => {
    const JOB = {
      id: 7, type: 'provider.try_image', status: 'failed', priority: 0, lane: 'conn:1', project_id: null,
      sequence_id: null, clip_id: null, parent_id: null, params: { prompt: 'a cat' }, progress: 30, stage: '',
      eta_at: null, client_ref: null, external_id: null, external_state: {}, attempt: 0, max_attempts: 3,
      next_attempt_at: null, error_code: 'internal_error', error_message: "Lỗi nội bộ không lường trước: KeyError: 'x'",
      hint: 'Xem nhật ký job để biết chi tiết.', result: {}, cost_estimate: { units: 1 }, cost_actual: {},
      created_at: '2026-11-19T12:00:00+00:00', started_at: null, finished_at: null, updated_at: null,
    };
    const CONN = {
      id: 1, kind: 'image', adapter: 'fake_image', name: 'Ảnh giả', base_url: '', api_key: '', has_api_key: false,
      default_model: '', options: {}, concurrency: 2, enabled: true,
      health: { status: 'alive', detail: 'ok', checked_at: new Date().toISOString() }, created_at: null, updated_at: null,
    };
    const routes: Record<string, unknown> = {
      '/api/auth/status': { authenticated: true, role: 'owner', via: 'local', local: true, password_set: { owner: false, viewer: false }, password_required_warning: true },
      '/api/ops/job.list': { items: [JOB], next_cursor: null, paused: false },
      '/api/ops/provider.list': { items: [CONN] },
      '/api/ops/provider.adapters': { items: [{ id: 'fake_image', kind: 'image', label: 'Ảnh giả (không tốn tiền)', config_fields: [], default_capabilities: {} }] },
      '/api/ops': [{ name: 'job.list' }],
      '/api/ops/settings.explain': [{ key: 'remote.allow_viewer', value: false, source: 'default' }],
      '/api/ops/settings.schema': [{ key: 'remote.allow_viewer', type: 'bool', default: false, description: 'x' }],
      '/api/ops/token.list': [],
      '/api/ops/session.list': [],
    };
    api.defaults.adapter = async (config) => ({
      data: routes[String(config.url)] ?? {}, status: 200, statusText: 'OK', headers: {}, config, request: {},
    });
    await render('/jobs');
    expect(container.textContent).toContain('Thử tạo ảnh');
    expect(container.textContent).toContain('Lỗi nội bộ không lường trước.');
    expect(container.textContent).not.toContain('KeyError');

    await render('/settings/providers');
    expect(container.textContent).toContain('Ảnh giả');
    expect(container.textContent).toContain('Hoạt động');

    await render('/settings/llm');
    expect(container.textContent).toContain('Chưa có');

    await render('/settings/access');
    expect(container.textContent).toContain('Chưa đặt mật khẩu — đừng mở ra Internet.');
    expect(container.textContent).toContain('Cho người xem truy cập từ xa');
    expect(container.textContent).toContain('mặc định');
  });

  it('trang Việc: tải trang sau lỗi → giữ danh sách đã tải, báo lỗi ngay dưới danh sách', async () => {
    const job = (id: number) => ({
      id, type: 'provider.try_tts', status: 'succeeded', priority: 0, lane: 'conn:1', project_id: null, sequence_id: null,
      clip_id: null, parent_id: null, params: {}, progress: 100, stage: '', eta_at: null, client_ref: null, external_id: null,
      external_state: {}, attempt: 0, max_attempts: 3, next_attempt_at: null, error_code: null, error_message: null, hint: null,
      result: {}, cost_estimate: {}, cost_actual: {}, created_at: null, started_at: null, finished_at: null, updated_at: null,
    });
    api.defaults.adapter = async (config) => {
      const url = String(config.url);
      if (url === '/api/ops/job.list') {
        const body = JSON.parse(String(config.data));
        if (body.cursor) {
          const { AxiosError } = await import('axios');
          throw new AxiosError('x', 'ERR_BAD_RESPONSE', config, {}, {
            data: { ok: false, error_code: 'internal_error', detail: 'Lỗi máy chủ.', status: 500 }, status: 500, statusText: '', headers: {}, config,
          });
        }
        return { data: { items: [job(9), job(8)], next_cursor: 8, paused: false }, status: 200, statusText: 'OK', headers: {}, config, request: {} };
      }
      const data = url === '/api/auth/status'
        ? { authenticated: true, role: 'owner', via: 'local', local: true, password_set: { owner: true, viewer: false } }
        : url === '/api/ops' ? [] : { items: [] };
      return { data, status: 200, statusText: 'OK', headers: {}, config, request: {} };
    };
    await render('/jobs');
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });
    expect(container.textContent).toContain('#9');
    expect(container.textContent).toContain('Không tải được các việc cũ hơn');
    expect(container.textContent).not.toContain('Không tải được danh sách việc');
  });

  it('trang trong dự án hiện "Sắp có" và điều hướng dự án', async () => {
    api.defaults.adapter = reply(404, { detail: 'Not Found' });
    await render('/p/3/studio');
    expect(container.textContent).toContain('Sắp có');
    expect(container.querySelector('nav[aria-label="Trong dự án"]')).not.toBeNull();
  });
});

const AUTH_OK = { authenticated: true, role: 'owner', via: 'local', local: true, password_set: { owner: true, viewer: false } };
const recent = (minAgo: number) => new Date(Date.now() - minAgo * 60_000).toISOString();

/** Backend giả cho khung v2: dự án 3 "Kiếp Hồ Ly", tập 12 + 13, lịch sử có 2 agent, 1 việc cần xử lý. */
function shellBackend() {
  const SEQ = (id: number, number: number, gates: Record<string, unknown> = {}) => ({
    id, project_id: 3, group_id: null, key: `ep${number}`, number, title: number === 12 ? 'Đêm trăng máu' : 'Người đưa đò',
    synopsis: '', script: 'x', status: 'draft', workflow_key: '', gates, pipeline: {},
    counts: { clips: 3, scenes: 1, duration_ms: 18000 },
  });
  const routes: Record<string, unknown> = {
    '/api/auth/status': AUTH_OK,
    '/api/ops/project.get': { id: 3, key: 'khl', name: 'Kiếp Hồ Ly', description: '', genre_key: 'serial', aspect: '9:16', language: 'vi', target_platforms: [], settings: {}, budget: {} },
    '/api/ops/project.list': { items: [], next_cursor: null },
    '/api/ops/sequence.list': { items: [SEQ(12, 12, { animatic: 'waiting' }), SEQ(13, 13)], next_cursor: null },
    '/api/ops/sequence.get': SEQ(12, 12, { animatic: 'waiting' }),
    '/api/ops/clip.list': {
      items: [
        { id: 1, sequence_id: 12, key: 'c1', image_take_id: 1, video_take_id: null, flags: { stale_image: true }, review: 'pending' },
        { id: 2, sequence_id: 12, key: 'c2', image_take_id: 2, video_take_id: null, flags: {}, review: 'pending' },
        { id: 3, sequence_id: 12, key: 'c3', image_take_id: 3, video_take_id: null, flags: {}, review: 'pending' },
      ],
      next_cursor: null,
    },
    '/api/ops/history.list': {
      items: [
        { id: 9, op_name: 'clip.update', actor: 'agent:biên-tập', summary: 'Sửa prompt 12 clip', created_at: recent(1), undone_at: null, entities: [] },
        { id: 8, op_name: 'clip.update', actor: 'agent:kiểm-định', summary: 'Đánh dấu lệch', created_at: recent(6), undone_at: null, entities: [] },
        { id: 7, op_name: 'sequence.update', actor: 'owner', summary: 'Đổi tên tập', created_at: recent(30), undone_at: null, entities: [] },
        { id: 6, op_name: 'clip.update', actor: 'agent:cũ', summary: 'Việc cũ', created_at: recent(45), undone_at: null, entities: [] },
      ],
      next_cursor: null,
    },
    '/api/ops/job.list': { items: [{ id: 1, status: 'needs_attention' }, { id: 2, status: 'succeeded' }], next_cursor: null, paused: false },
    '/api/ops/budget.status': {
      image: { enabled: true, total: 1236, spent: 412, remaining: 824 },
      video: { enabled: false, total: null, spent: 0, remaining: null },
    },
    '/api/ops': [],
  };
  api.defaults.adapter = async (config) => ({
    data: routes[String(config.url)] ?? {}, status: 200, statusText: 'OK', headers: {}, config, request: {},
  });
}

const settle = () =>
  act(async () => {
    await new Promise((r) => setTimeout(r, 60));
  });

describe('khung v2 (dây chuyền, Cần bạn, agent)', () => {
  it('rail mới theo thứ tự làm phim + nhóm phụ; thanh dưới điện thoại mới', async () => {
    shellBackend();
    await render('/p/3/overview');
    await settle();
    const rail = Array.from(container.querySelectorAll('nav[aria-label="Trong dự án"]')).at(-1)!;
    const labels = Array.from(rail.querySelectorAll('a')).map((a) => a.textContent?.replace(/\d+$/, ''));
    expect(labels).toEqual(['Tổng quan', 'Câu chuyện', 'Thư viện', 'Duyệt', 'Xuất', 'Hoạt động', 'Lịch sử', 'Cài đặt']);
    expect(rail.querySelector('hr')).not.toBeNull();
    const bottom = container.querySelector('nav[aria-label="Điều hướng chính"]')!;
    const items = Array.from(bottom.querySelectorAll('a, button')).map((a) => a.textContent?.replace(/\d+/g, ''));
    expect(items).toEqual(['Cần bạn', 'Duyệt', 'Tập', 'Việc', 'Thêm']);
    expect(bottom.querySelector('a[aria-label="Cần bạn: 2 việc chờ bạn"]')).not.toBeNull();
  });

  it('/p/:id → overview; /p/:id/sequences → overview, giữ query', async () => {
    shellBackend();
    let router = await render('/p/3');
    expect(router.state.location.pathname).toBe('/p/3/overview');
    // Tổng quan do module `pages/overview` (Task H) cung cấp: ma trận thay trang giữ chỗ.
    expect(container.textContent).toContain('Tập theo chặng');
    router = await render('/p/3/sequences?arc=2');
    expect(router.state.location.pathname).toBe('/p/3/overview');
    expect(router.state.location.search).toBe('?arc=2');
  });

  it('topbar: tên dự án thật, đồng hồ chi phí, chip agent và Cần bạn đếm từ dữ liệu', async () => {
    shellBackend();
    await render('/p/3/overview');
    await settle();
    expect(container.textContent).toContain('Kiếp Hồ Ly');
    expect(container.textContent).not.toContain('Dự án 3');
    // 2 agent trong 10 phút (agent:cũ 45 phút trước không tính), agent:biên-tập vừa ghi → "đang ghi".
    const agent = container.querySelector('button[popovertarget="sr-activity-panel"]')!;
    expect(agent.getAttribute('aria-label')).toContain('2 agent trong 10 phút, đang ghi');
    // Cần bạn = 1 gate chờ (tập 12, animatic) + 1 việc needs_attention.
    const need = container.querySelector('a[aria-label^="Cần bạn:"]')!;
    expect(need.getAttribute('aria-label')).toBe('Cần bạn: 1 gate chờ duyệt, 1 việc cần xử lý');
    expect(need.getAttribute('href')).toBe('/p/3/activity');
    expect(container.querySelector('a[href="/settings/budget"]')?.textContent).toContain('412');
    // Panel Hoạt động: agent + change set có Hoàn tác.
    const panel = document.getElementById('sr-activity-panel')!;
    expect(panel.textContent).toContain('biên-tập');
    expect(panel.querySelectorAll('button[aria-label^="Hoàn tác:"]').length).toBe(4);
  });

  it('thanh chặng hiện ở trang của tập và ở review với đúng một ?seq=, ẩn ở chỗ khác', async () => {
    shellBackend();
    await render('/p/3/s/12');
    await settle();
    const spine = () => container.querySelector('[data-spine]');
    expect(spine()).not.toBeNull();
    const stages = spine()!.querySelectorAll('nav ol > li > a');
    expect(stages).toHaveLength(8);
    expect(stages[1].getAttribute('aria-current')).toBe('page');
    expect(stages[2].getAttribute('aria-label')).toBe('Chặng 3: Khung hình — có clip đã cũ · 1 cũ (Alt 3)');
    expect(stages[3].getAttribute('aria-label')).toContain('chờ bạn');
    expect(spine()!.textContent).toContain('Đêm trăng máu');

    await render('/p/3/review?seq=12&part=video');
    await settle();
    expect(spine()).not.toBeNull();
    expect(spine()!.querySelector('[aria-current="page"]')?.getAttribute('href')).toBe('/p/3/review?seq=12&part=video');

    await render('/p/3/review?seq=12,13');
    await settle();
    expect(spine()).toBeNull();
    await render('/p/3/library');
    await settle();
    expect(spine()).toBeNull();
  });

  it('phím: ] tập kế, [ tập trước, Alt 3 chặng Khung hình, G rồi L Thư viện; không chạy khi đang gõ', async () => {
    shellBackend();
    const router = await render('/p/3/s/12');
    await settle();
    const press = (key: string, init: KeyboardEventInit = {}) =>
      act(() => {
        window.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init }));
      });
    press(']');
    expect(router.state.location.pathname).toBe('/p/3/s/13');
    press('[');
    expect(router.state.location.pathname).toBe('/p/3/s/12');
    press('3', { altKey: true, code: 'Digit3' });
    expect(router.state.location.pathname + router.state.location.search).toBe('/p/3/review?seq=12&part=image');
    press('g');
    press('l');
    expect(router.state.location.pathname).toBe('/p/3/library');

    const input = document.createElement('input');
    container.appendChild(input);
    input.focus();
    act(() => {
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'g', bubbles: true }));
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'o', bubbles: true }));
    });
    expect(router.state.location.pathname).toBe('/p/3/library');
  });

  it('người xem (history/job bị 403) → chip không lỗi, khung vẫn dựng', async () => {
    shellBackend();
    const ok = api.defaults.adapter as AxiosAdapter;
    api.defaults.adapter = async (config) => {
      if (/history\.list|job\.list/.test(String(config.url))) {
        const { AxiosError } = await import('axios');
        throw new AxiosError('x', 'ERR_BAD_RESPONSE', config, {}, {
          data: { ok: false, error_code: 'forbidden', detail: 'Không có quyền.', status: 403 }, status: 403, statusText: '', headers: {}, config,
        });
      }
      return ok(config);
    };
    await render('/p/3/overview');
    await settle();
    expect(container.textContent).toContain('Kiếp Hồ Ly');
    expect(container.querySelector('a[aria-label^="Cần bạn:"]')?.getAttribute('aria-label')).toBe('Cần bạn: 1 gate chờ duyệt');
  });
});

describe('safeNext', () => {
  it('chỉ nhận đường dẫn tương đối cùng origin', () => {
    expect(safeNext('/jobs?x=1')).toBe('/jobs?x=1');
    expect(safeNext('//evil.com')).toBe('/projects');
    expect(safeNext('/\\evil.com')).toBe('/projects');
    expect(safeNext('https://evil.com')).toBe('/projects');
    expect(safeNext('/login?next=%2F')).toBe('/projects');
    expect(safeNext(null)).toBe('/projects');
  });

  it('chặn ký tự điều khiển (%09, %0A) và gạch ngược mà trình duyệt bỏ/đổi khi phân tích URL', () => {
    const decoded = new URLSearchParams('next=%2F%09%2Fevil.com').get('next');
    expect(decoded).toBe('/\t/evil.com');
    expect(safeNext(decoded)).toBe('/projects');
    expect(safeNext('/\n/evil.com')).toBe('/projects');
    expect(safeNext('/a\\b')).toBe('/projects');
    expect(safeNext('/\u007f')).toBe('/projects');
  });

  it('kiểm tra origin bằng URL và trả đường dẫn đã chuẩn hoá', () => {
    expect(safeNext('/p/3/studio#take', 'http://127.0.0.1:8990')).toBe('/p/3/studio#take');
    expect(safeNext('/./jobs', 'http://127.0.0.1:8990')).toBe('/jobs');
    expect(safeNext('/../login', 'http://127.0.0.1:8990')).toBe('/projects');
  });
});
