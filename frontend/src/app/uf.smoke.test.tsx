import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { api } from '../api/client';
import { routes } from './routes';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const originalAdapter = api.defaults.adapter;
let container: HTMLDivElement;
let root: Root;
let calls: { url: string; body: unknown }[] = [];

const AUTH_OK = { authenticated: true, role: 'owner', via: 'local', local: true, password_set: { owner: true, viewer: false } };

function backend(extra: Record<string, unknown>) {
  calls = [];
  const routesMap: Record<string, unknown> = { '/api/auth/status': AUTH_OK, '/api/ops': [], ...extra };
  api.defaults.adapter = async (config) => {
    calls.push({ url: String(config.url), body: config.data ? JSON.parse(String(config.data)) : null });
    const v = routesMap[String(config.url)];
    return { data: typeof v === 'function' ? (v as (b: unknown) => unknown)(config.data) : v ?? {}, status: 200, statusText: 'OK', headers: {}, config, request: {} };
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
  await act(async () => {
    await new Promise((r) => setTimeout(r, 60));
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

describe('Cài đặt › Lưu trữ', () => {
  it('xem trước dọn take, thùng rác có file, cache dựng', async () => {
    backend({
      '/api/ops/media.cleanup_preview': { files: 12, bytes: 3 * 1024 * 1024, by_kind: { image: { files: 10, bytes: 1 }, video: { files: 2, bytes: 1 } }, items: [], truncated: false, confirm_token: 't' },
      '/api/ops/trash.list': {
        items: [{ media_id: 5, kind: 'video', size: 2048, path: 'media/p1/v_5.mp4', project_id: 1, trashed_at: '2026-11-19T10:00:00Z', file_present: true }],
        next_cursor: null,
        total: { files: 1, bytes: 2048 },
      },
      '/api/ops/trash.purge_preview': { files: 1, bytes: 2048, by_kind: {}, items: [], truncated: false, confirm_token: 'p' },
      '/api/ops/render.cache_stats': { segments: { files: 6, bytes: 5928667 }, chunks: { files: 3, bytes: 1 }, work: { files: 0, bytes: 0 }, total: { files: 9, bytes: 5928668 } },
    });
    await render('/settings/storage');
    const text = container.textContent ?? '';
    expect(text).toContain('Lưu trữ');
    expect(text).toContain('12 file');
    expect(text).toContain('10 ảnh · 2 video');
    expect(text).toContain('v_5.mp4');
    expect(text).toContain('Xoá hẳn khỏi đĩa');
    expect(text).toContain('Đoạn dựng');
    // mục "Lưu trữ" trong cột mục cài đặt
    expect(container.querySelector('nav[aria-label="Mục cài đặt"] a[href="/settings/storage"]')).not.toBeNull();
    expect(calls.find((c) => c.url.endsWith('media.cleanup_preview'))?.body).toMatchObject({ filters: { unselected_takes_older_than_days: 30 } });
  });
});

describe('Kịch bản của tập (s/:seqId/script)', () => {
  it('sửa tay kịch bản + viết bằng AI, thanh chặng tô chặng Kịch bản', async () => {
    const SEQ = {
      id: 12, project_id: 3, group_id: null, key: 'ep12', number: 12, title: 'Đêm trăng máu', synopsis: 'Bé An lạc trong rừng.',
      script: 'CẢNH 1. Rừng đêm.', status: 'writing', workflow_key: '', gates: {}, pipeline: { memory: {} }, counts: { clips: 0 },
      updated_at: '2026-11-20T00:00:00Z',
    };
    backend({
      '/api/ops/project.get': { id: 3, name: 'Kiếp Hồ Ly', counts: {} },
      '/api/ops/sequence.get': SEQ,
      '/api/ops/sequence.list': { items: [SEQ], next_cursor: null },
      '/api/ops/job.list': { items: [], next_cursor: null },
      '/api/ops/history.list': { items: [], next_cursor: null },
      '/api/ops/pipeline.status': { items: [], next_cursor: null },
      '/api/ops/preset.list': { items: [] },
    });
    await render('/p/3/s/12/script');
    expect(container.querySelector('h1')?.textContent).toBe('Kịch bản · Tập 12');
    expect((container.querySelector('textarea') as HTMLTextAreaElement).value).toBe('CẢNH 1. Rừng đêm.');
    expect(container.textContent).toContain('Viết lại bằng AI');
    const spine = container.querySelector('[data-spine]');
    expect(spine?.querySelector('[aria-current="page"]')?.getAttribute('href')).toBe('/p/3/s/12/script');
  });
});
