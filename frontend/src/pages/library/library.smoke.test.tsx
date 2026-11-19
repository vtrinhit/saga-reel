import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { AxiosAdapter, InternalAxiosRequestConfig } from 'axios';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { api } from '../../api/client';
import { routes } from '../../app/routes';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const originalAdapter = api.defaults.adapter;
let container: HTMLDivElement;
let root: Root;
let calls: { op: string; body: Record<string, unknown> }[] = [];

const project = {
  id: 1, key: 'kiep-ho-ly', name: 'Kiếp Hồ Ly', description: '', genre_key: 'serial', aspect: '9:16', resolution: '1080p',
  fps: 30, language: 'vi', languages: ['vi'], target_platforms: ['tiktok'], style_block: 'ink wash', negative_block: '',
  settings: {}, budget: {}, cover_media_id: null, created_at: '2026-11-20T00:00:00', updated_at: '2026-11-20T00:00:00',
  counts: { assets: 2, sequences: 36, clips: 410 },
};
const look = {
  id: 11, asset_id: 5, key: 'default', name: 'Áo dài đỏ', description_delta: '', keep_features: ['dây đỏ trên tóc'],
  lock_word: 'crimson silk ao dai', anchor_media_id: 100, sheet: { closeup: 101 }, ref_media_ids: [], valid_from: 1,
  valid_to: 40, is_default: true, image_count: 2, warnings: [], updated_at: '2026-11-20T00:00:00',
};
const asset = {
  id: 5, project_id: 1, kind: 'character', key: 'AN', name: 'Lạc An', description: 'Thiếu nữ hồ ly', locked: true,
  voice_profile_id: null, tags: [], custom: {}, cover_media_id: null, updated_at: '2026-11-20T00:00:00', looks: [look],
};
const prop = { ...asset, id: 6, kind: 'prop', key: 'KIEM', name: 'Kiếm gỗ', locked: false, looks: [{ ...look, id: 12, asset_id: 6 }] };
const kinds = [
  { id: 1, key: 'character', label: 'Nhân vật', views: ['anchor', 'closeup', 'profile', 'back', 'three_quarter'], default_views: ['anchor', 'closeup', 'profile', 'back', 'three_quarter'] },
  { id: 3, key: 'prop', label: 'Đạo cụ', views: ['anchor', 'detail'], default_views: ['anchor', 'detail'] },
];

function opReply(op: string, body: Record<string, unknown>): unknown {
  switch (op) {
    case 'project.list': return { items: [project], next_cursor: null };
    case 'project.get': return project;
    case 'project.update': return { ...project, ...(body.values as object), updated_at: '2026-11-20T01:00:00' };
    case 'preset.list':
      return body.kind === 'genre'
        ? { items: [{ id: 1, kind: 'genre', key: 'serial', name: 'Phim bộ (serial)', data: { default_platforms: ['tiktok'] } }] }
        : { items: [{ id: 2, kind: 'platform', key: 'tiktok', name: 'TikTok', data: {} }, { id: 3, kind: 'platform', key: 'yt-shorts', name: 'YouTube Shorts', data: {} }] };
    case 'asset_kind.list': return { items: kinds };
    case 'asset.list': return { items: [asset, prop], next_cursor: null };
    case 'asset.get': return asset;
    case 'sheet.list':
      return {
        look, selected: { anchor: 100, closeup: 101 },
        views: { anchor: [{ id: 1, view: 'anchor', media_id: 100, status: 'succeeded', job_id: 1, cost: 1, created_at: null, selected: true }] },
      };
    case 'voice.list': return { items: [{ id: 9, project_id: 1, key: 'hoaimy', name: 'Hoài My', voice: 'vi-VN-HoaiMyNeural', connection: 'edge' }] };
    case 'bible.list': return { items: [{ id: 3, type: 'character', key: 'thang-ti', summary: 'Cậu bé chăn trâu', attributes: { name: 'Thằng Tí' }, status: 'pending', asset_id: null }] };
    case 'sequence.list': return { items: [] };
    case 'budget.status': return { image: { enabled: true, total: 60, spent: 12, remaining: 48 }, video: { enabled: true, total: null, spent: 0, remaining: null } };
    case 'look.preview_prompt': return { text: 'PROMPT', refs: [], warnings: [], provider_caps: {} };
    default: return { items: [] };
  }
}

const adapter: AxiosAdapter = async (config: InternalAxiosRequestConfig) => {
  const url = config.url ?? '';
  let data: unknown;
  if (url.includes('/api/auth/status')) {
    data = { authenticated: true, role: 'owner', via: 'local', local: true, password_set: { owner: false, viewer: false } };
  } else if (url.startsWith('/api/ops/')) {
    const op = url.slice('/api/ops/'.length);
    const body = typeof config.data === 'string' ? JSON.parse(config.data || '{}') : (config.data ?? {});
    calls.push({ op, body });
    data = opReply(op, body);
  } else {
    data = { items: [] };
  }
  return { data, status: 200, statusText: 'OK', headers: {}, config, request: {} };
};

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
    await new Promise((r) => setTimeout(r, 40));
  });
  return router;
}

beforeEach(() => {
  calls = [];
  api.defaults.adapter = adapter;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  api.defaults.adapter = originalAdapter;
});

describe('Task G (smoke)', () => {
  it('/projects: bốn lối tạo + thẻ dự án có số tập/clip', async () => {
    await render('/projects');
    const t = container.textContent ?? '';
    expect(t).toContain('Từ một ý tưởng');
    expect(t).toContain('Dự án trống');
    expect(t).toContain('Kiếp Hồ Ly');
    expect(t).toContain('Phim bộ (serial)');
    expect(t).toMatch(/36\s*tập/);
  });

  it('/p/1/library: tab loại có số đếm, thẻ tài sản, gợi ý bible chờ xác nhận', async () => {
    await render('/p/1/library');
    const t = container.textContent ?? '';
    expect(t).toContain('Nhân vật 1');
    expect(t).toContain('Đạo cụ 1');
    expect(t).toContain('Giọng 1');
    expect(t).toContain('Lạc An');
    expect(t).not.toContain('Kiếm gỗ');
    expect(t).toContain('Thằng Tí');
    expect(t).toContain('chờ xác nhận');
    const card = container.querySelector('a[href="/p/1/library/5"]');
    expect(card).not.toBeNull();
  });

  it('/p/1/library/5: chi tiết — mô tả khoá, look, lưới sheet đủ góc, cụm khoá', async () => {
    await render('/p/1/library/5');
    const t = container.textContent ?? '';
    expect(t).toContain('Đã khoá mô tả');
    expect(t).toContain('Thiếu nữ hồ ly');
    expect(t).toContain('Áo dài đỏ · tập 1–40');
    for (const v of ['Mỏ neo', 'Cận mặt', 'Nghiêng', 'Sau lưng', 'Ba phần tư']) expect(t).toContain(v);
    expect(t).toContain('Tạo 3 góc còn thiếu');
    const lock = container.querySelector<HTMLInputElement>('#look-11-lock');
    expect(lock?.value).toBe('crimson silk ao dai');
    expect(t).toContain('dây đỏ trên tóc');
    expect(calls.some((c) => c.op === 'sheet.list' && c.body.look_id === 11)).toBe(true);
  });

  it('/p/1/settings: lưu chỉ gửi trường đã đổi kèm expected_updated_at', async () => {
    await render('/p/1/settings');
    const style = container.querySelector<HTMLTextAreaElement>('#ps-style-block');
    expect(style?.value).toBe('ink wash');
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!;
      setter.call(style, 'ink wash, jade palette');
      style!.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(container.textContent).toContain('1 mục chưa lưu');
    const form = container.querySelector('form');
    await act(async () => {
      form!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
      await new Promise((r) => setTimeout(r, 20));
    });
    const upd = calls.find((c) => c.op === 'project.update');
    expect(upd?.body).toEqual({ id: 1, values: { style_block: 'ink wash, jade palette' }, expected_updated_at: '2026-11-20T00:00:00' });
  });
});
