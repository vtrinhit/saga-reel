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

  it('backend chưa có /api/auth (404) → shell vẫn dùng được', async () => {
    api.defaults.adapter = reply(404, { detail: 'Not Found' });
    await render('/jobs');
    expect(container.textContent).toContain('Đang xây');
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
});
