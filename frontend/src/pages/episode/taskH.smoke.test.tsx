import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { AxiosAdapter } from 'axios';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { api } from '../../api/client';
import { ImportWizard } from '../importer/ImportWizard';
import { Overview } from '../overview/Overview';
import { Review } from '../review/Review';
import { Episode } from './Episode';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const originalAdapter = api.defaults.adapter;
let container: HTMLDivElement;
let root: Root;
const calls: { op: string; body: Record<string, unknown> }[] = [];

const clip = (id: number, extra: Record<string, unknown> = {}) => ({
  id, sequence_id: 4, scene_id: 1, rank: `a${id}`, key: `c${String(id).padStart(4, '0')}`, title: '',
  image_prompt: `Prompt ${id}`, motion_prompt: '', image_prompt_src: {}, motion_prompt_src: {}, prompt_mode: 'synced', assets: [],
  engine: 'ai_video', duration_ms: 4000, link: 'cut', image_take_id: id === 1 ? 11 : null, video_take_id: null, flags: {},
  review: 'pending', notes: '', shot_size: 'ms', angle: '', camera_move: '', lens: '', composition: '', eyeline: '', screen_dir: '',
  custom: {}, updated_at: '2026-11-20T00:00:00Z', take_counts: id === 1 ? { image: 2 } : {},
  selected_takes: { image: null, video: null }, ...extra,
});
const take = (id: number) => ({
  id, owner_type: 'clip', owner_id: 1, part: 'image', media_id: id, status: 'succeeded', job_id: null, inputs: { seed: 42 },
  parent_take_id: null, cost: 1, elapsed_ms: 10, rating: null, verdict: '', notes: '', auto_score: {}, created_at: '',
  media: { id, kind: 'image', url: `/media/${id}`, thumb_url: `/media/${id}/thumb`, width: 360, height: 640, duration_ms: null },
});
const seq = { id: 4, project_id: 1, group_id: null, key: 'ep12', number: 12, title: 'Đêm trăng máu', synopsis: '', script: 'x', status: 'draft', workflow_key: '', gates: {}, pipeline: {}, counts: { clips: 3, scenes: 1, duration_ms: 12000 } };

function backend(): AxiosAdapter {
  return async (config) => {
    const url = config.url ?? '';
    const op = url.replace('/api/ops/', '');
    const body = typeof config.data === 'string' && config.data ? JSON.parse(config.data) : {};
    calls.push({ op, body });
    const data = ((): unknown => {
      switch (op) {
        case 'project.get':
          return { id: 1, key: 'p', name: 'Kiếp Hồ Ly', aspect: '9:16', genre_key: 'serial', target_platforms: ['tiktok'], counts: {} };
        case 'review.queue':
          return { items: [clip(1), clip(2), clip(3)], next_cursor: null };
        case 'entity.list':
          return { items: body.type === 'scene' ? [{ id: 1, sequence_id: 4, key: 's01', rank: 'a', title: 'Miếu hoang', summary: '' }] : [], next_cursor: null };
        case 'sequence.lint':
          return { sequence_id: 4, clips: 3, warnings: [{ code: 'same_size_repeat', level: 'warning', clip_id: 2, clip_key: 'c0002', message: 'Clip c0002 cùng cỡ với c0001' }], counts: {} };
        case 'sequence.list':
          return { items: [seq], next_cursor: null };
        case 'sequence.cost_summary':
          return { sequence_id: 4, total: 6, takes: 6, succeeded: 6, retries: 2, by_part: {}, per_clip: [{ clip_id: 1, clip_key: 'c0001', cost: 2, takes: 2 }] };
        case 'take.list':
          return { items: [take(12), take(11)], selected: { image: 11 } };
        case 'pipeline.status':
          return { items: [] };
        case 'budget.status':
          return { image: { enabled: true, total: 60, spent: 3, remaining: 57 }, video: { enabled: false, total: null, spent: 0, remaining: null } };
        case 'asset.list':
        case 'export.list':
        case 'job.list':
          return { items: [] };
        default:
          return {};
      }
    })();
    return { data, status: 200, statusText: 'OK', headers: {}, config, request: {} };
  };
}

async function render(path: string, pattern: string, el: React.ReactNode) {
  const router = createMemoryRouter([{ path: pattern, element: el }], { initialEntries: [path] });
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
  calls.length = 0;
  api.defaults.adapter = backend();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  // jsdom không có ResizeObserver / matchMedia
  (globalThis as { ResizeObserver?: unknown }).ResizeObserver ??= class { observe() {} disconnect() {} unobserve() {} };
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  api.defaults.adapter = originalAdapter;
});

describe('Task H — smoke', () => {
  it('bảng clip: toolbar, lint, nạp clip qua review.queue mọi trạng thái duyệt', async () => {
    await render('/p/1/s/4', '/p/:projectId/s/:seqId', <Episode />);
    expect(container.textContent).toContain('Lưới');
    expect(container.textContent).toContain('Xuất shot list');
    expect(container.textContent).toContain('1 cảnh báo nhịp');
    const q = calls.find((c) => c.op === 'review.queue')!;
    expect((q.body.filter as { review: string[] }).review).toEqual(['pending', 'approved', 'rejected', 'skipped']);
    expect(container.textContent).toContain('3 clip');
  });

  it('duyệt: hàng đợi, take, thanh quyết định và phím A gọi take.verdict keep', async () => {
    await render('/p/1/review?seq=4&part=image', '/p/:projectId/review', <Review />);
    await act(async () => {
      await new Promise((r) => setTimeout(r, 150));
    });
    expect(container.querySelector('[role=listbox][aria-label="Các take"]')).not.toBeNull();
    expect(container.textContent).toContain('Hàng đợi');
    expect(container.textContent).toContain('Chọn take');
    expect(container.textContent).toContain('2 lượt qua 2 take');
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'a' }));
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(calls.some((c) => c.op === 'take.verdict' && c.body.verdict === 'keep')).toBe(true);
    expect(calls.some((c) => c.op === 'clip.update' && (c.body.values as { review?: string }).review === 'approved')).toBe(true);
  });

  it('nhập hàng loạt: bước nguồn có dán chữ / file / thư mục ảnh', async () => {
    await render('/p/1/import?seq=4', '/p/:projectId/import', <ImportWizard />);
    expect(container.textContent).toContain('Nhập hàng loạt');
    expect(container.textContent).toContain('Dán chữ');
    expect(container.textContent).toContain('Thư mục ảnh');
    expect(container.querySelector('#imp-text')).not.toBeNull();
  });

  it('tổng quan: số liệu thật + ma trận tập', async () => {
    await render('/p/1/overview', '/p/:projectId/overview', <Overview />);
    expect(container.textContent).toContain('Kiếp Hồ Ly');
    expect(container.textContent).toContain('Tập theo chặng');
    expect(container.querySelector('[role=region][aria-label="Ma trận tập × chặng"]')).not.toBeNull();
    expect(container.textContent).toContain('3 / 60');
    expect(container.textContent).toContain('lượt ảnh tháng này');
  });
});
