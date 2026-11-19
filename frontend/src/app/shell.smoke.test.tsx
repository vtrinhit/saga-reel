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

  it('trang trong dự án hiện "Sắp có" và điều hướng dự án', async () => {
    api.defaults.adapter = reply(404, { detail: 'Not Found' });
    await render('/p/3/studio');
    expect(container.textContent).toContain('Sắp có');
    expect(container.querySelector('nav[aria-label="Trong dự án"]')).not.toBeNull();
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
