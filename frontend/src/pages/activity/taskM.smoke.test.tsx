import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { api } from '../../api/client';
import { HistoryPage } from '../history/HistoryPage';
import { PresetsPage } from '../presets/PresetsPage';
import { SettingsStorage } from '../settings/SettingsStorage';
import { StoryPage } from '../story/StoryPage';
import { InboxPage, ProjectActivityPage } from './ActivityPage';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const originalAdapter = api.defaults.adapter;
let container: HTMLDivElement;
let root: Root;
let qc: QueryClient;
let calls: { op: string; body: Record<string, unknown> }[];

const steps = [
  { name: 'outline', label: 'Lập dàn ý', status: 'done', enabled: true },
  { name: 'gate:outline', label: 'Duyệt outline', status: 'running', enabled: true },
  { name: 'write_script', label: 'Viết kịch bản', status: 'pending', enabled: true },
  { name: 'generate_images', label: 'Tạo ảnh', status: 'pending', enabled: true },
  { name: 'gate:storyboard', label: 'Duyệt storyboard', status: 'pending', enabled: true },
];

const OPS: Record<string, unknown> = {
  'project.list': { items: [{ id: 3, name: 'Kiếp Hồ Ly' }], next_cursor: null },
  'project.get': { id: 3, name: 'Kiếp Hồ Ly', genre_key: 'serial' },
  'pipeline.status': {
    items: [
      { sequence_id: 12, number: 12, title: 'Đêm trăng máu', workflow: 'storyboard_first', status: 'waiting_review', job_id: 1, current: 'gate:outline', waiting: { gate: 'outline', since: '2026-11-20T10:00:00+00:00' }, error: null, steps, gates: {}, progress: 20, updated_at: '2026-11-20T10:00:00+00:00' },
      { sequence_id: 13, number: 13, title: 'Tập 13', workflow: 'full_auto', status: 'failed', job_id: 2, current: 'outline', waiting: null, error: { message: 'LLM trả JSON sai' }, steps, gates: {}, progress: 10, updated_at: '2026-11-20T09:00:00+00:00' },
    ],
  },
  'job.list': { items: [{ id: 9, type: 'generate.image', status: 'failed', project_id: 3, sequence_id: 12, params: {}, error_message: 'MaiVideo 502', updated_at: '2026-11-20T10:00:00Z' }], next_cursor: null, paused: false },
  'history.list': {
    items: [
      { id: 7, op_name: 'clip.bulk_update', actor: 'agent:biên-tập', summary: 'sửa prompt 12 clip', created_at: new Date().toISOString(), undone_at: null, entities: [{ type: 'clip', id: 4, revision_id: 70 }] },
      { id: 6, op_name: 'look.update', actor: 'user:owner', summary: 'sửa cụm khoá look', created_at: new Date().toISOString(), undone_at: null, entities: [{ type: 'look', id: 2, revision_id: 60 }] },
    ],
    next_cursor: null,
  },
  'token.list': [{ id: 1, name: 'biên-tập', role: 'owner', active: true, last_used_at: null }],
  'pipeline.run': { dry_run: true, workflow: 'storyboard_first', step_keys: ['write_script', 'generate_images', 'gate:storyboard'], steps: [], sequences: [{ sequence_id: 12, clips: 48, images: 48, videos: 0, llm_calls: 1, status: 'waiting_review' }], estimate: { images: 48, videos: 0, llm_calls: 1 } },
  'sequence.status': { sequence_id: 12, number: 12, title: 'Đêm trăng máu', has_script: true, clips: { total: 0, with_image: 0, with_video: 0, image_prompt: 0, motion_prompt: 0, approved: 0, flagged: 0, seconds: 0 }, active_jobs: 0 },
  'gate.approve': { gate: 'outline', resumed: true },
  'bible.list': {
    items: [
      { id: 1, project_id: 3, type: 'fact', key: 'chuong_dong', summary: 'Chuông đồng chỉ kêu khi trăng đỏ', attributes: {}, status: 'pending', locked: false, story_order: 3, asset_id: null, evidence_count: 1 },
      { id: 2, project_id: 3, type: 'relationship', key: 'khang_an', summary: 'Minh Khang không biết An là hồ ly', attributes: {}, status: 'confirmed', locked: true, story_order: 1, asset_id: null, evidence_count: 0 },
    ],
    next_cursor: null,
  },
  'sequence.list': { items: [{ id: 12, project_id: 3, number: 12, title: 'Đêm trăng máu', synopsis: 'Khang gặp An', script: 'Cảnh 1 · Phố Hội, mưa đêm.', workflow_key: 'storyboard_first', gates: {}, pipeline: {}, counts: {} }], next_cursor: null },
  'sequence.get': { id: 12, project_id: 3, number: 12, title: 'Đêm trăng máu', synopsis: 'Khang gặp An', script: 'Cảnh 1 · Phố Hội, mưa đêm.', workflow_key: 'storyboard_first', gates: {}, pipeline: {}, counts: {} },
  'preset.list': { items: [
    { id: 1, kind: 'platform', key: 'tiktok', name: 'TikTok', data: { label: 'TikTok', width: 1080, duration_s: { min: 3, max: 600, recommended: [15, 60] } }, builtin: true, modified: true, project_id: null },
  ] },
  'render.cache_stats': { segments: { files: 2, bytes: 2048 }, chunks: { files: 0, bytes: 0 }, work: { files: 1, bytes: 10 }, total: { files: 3, bytes: 2058 } },
};

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  calls = [];
  api.defaults.adapter = async (config) => {
    const url = String(config.url);
    const op = url.replace('/api/ops/', '');
    const body = config.data ? JSON.parse(String(config.data)) : {};
    calls.push({ op, body });
    if (url === '/api/auth/status') {
      return { data: { authenticated: true, role: 'owner', via: 'local', local: true, password_set: { owner: false, viewer: false } }, status: 200, statusText: 'OK', headers: {}, config, request: {} };
    }
    const data = op in OPS ? OPS[op] : { items: [], next_cursor: null };
    return { data, status: 200, statusText: 'OK', headers: {}, config, request: {} };
  };
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  api.defaults.adapter = originalAdapter;
});

const flush = () => act(async () => void (await new Promise((r) => setTimeout(r, 30))));

async function render(path: string, route: string, el: React.ReactNode) {
  act(() =>
    root.render(
      <QueryClientProvider client={qc}>
        <MemoryRouter key={path} initialEntries={[path]}>
          <Routes>
            <Route path={route} element={el} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    ),
  );
  await flush();
  await flush();
}

const text = () => document.body.textContent ?? '';
const button = (label: string) => Array.from(document.querySelectorAll('button')).find((b) => b.textContent?.includes(label)) as HTMLButtonElement | undefined;

describe('Task M — smoke', () => {
  it('Hoạt động: GateCard có giá bước kế, NeedItem, agent, lịch sử; duyệt gọi gate.approve', async () => {
    await render('/p/3/activity', '/p/:projectId/activity', <ProjectActivityPage />);
    expect(text()).toContain('Duyệt dàn ý · Tập 12 · Đêm trăng máu');
    expect(text()).toContain('Nếu duyệt, chạy tiếp chặng Kịch bản');
    expect(text()).toContain('48 ảnh');
    expect(text()).toContain('Quy trình Tập 13 dừng vì lỗi');
    expect(text()).toContain('biên-tập');
    expect(text()).toContain('sửa cụm khoá look');
    await act(async () => button('Duyệt và chạy tiếp')!.click());
    await flush();
    expect(calls.find((c) => c.op === 'gate.approve')?.body).toMatchObject({ sequence_id: 12, gate: 'outline' });
  });

  it('Agent: tạm dừng một agent (token.suspend) và tạm dừng mọi agent (agents.pause_all)', async () => {
    await render('/p/3/activity', '/p/:projectId/activity', <ProjectActivityPage />);
    await act(async () => (document.querySelector('button[aria-label="Tạm dừng agent biên-tập"]') as HTMLButtonElement).click());
    await flush();
    expect(calls.find((c) => c.op === 'token.suspend')?.body).toEqual({ name: 'biên-tập' });
    await act(async () => button('Tạm dừng mọi agent')!.click());
    await flush();
    expect(calls.some((c) => c.op === 'agents.pause_all')).toBe(true);
    expect(document.querySelector('a[href="/p/3/history?actor=agent%3Abi%C3%AAn-t%E1%BA%ADp"]')).not.toBeNull();
  });

  it('Trả lại gate cần góp ý', async () => {
    await render('/p/3/activity', '/p/:projectId/activity', <ProjectActivityPage />);
    await act(async () => button('Trả lại kèm góp ý')!.click());
    expect(document.body.textContent).toContain('Gửi góp ý và chạy lại');
    expect(button('Gửi góp ý và chạy lại')!.disabled).toBe(true);
  });

  it('/inbox gom mọi dự án bằng một pipeline.status toàn app (không vòng theo dự án)', async () => {
    await render('/inbox', '/inbox', <InboxPage />);
    const ps = calls.filter((c) => c.op === 'pipeline.status');
    expect(ps).toHaveLength(1);
    expect(ps[0].body).toMatchObject({ active_only: true });
    expect(ps[0].body.project_id).toBeUndefined();
    expect(text()).toContain('Kiếp Hồ Ly');
  });

  it('Lịch sử: change set + hoàn tác đúng id', async () => {
    await render('/p/3/history', '/p/:projectId/history', <HistoryPage />);
    expect(text()).toContain('sửa prompt 12 clip');
    await act(async () => (document.querySelector('button[aria-label="Hoàn tác: sửa prompt 12 clip"]') as HTMLButtonElement).click());
    await flush();
    expect(calls.find((c) => c.op === 'history.undo')?.body).toMatchObject({ change_set_id: 7, project_id: 3 });
  });

  it('Câu chuyện: bible chờ xác nhận + kịch bản tập', async () => {
    await render('/p/3/story', '/p/:projectId/story', <StoryPage />);
    expect(text()).toContain('Chuông đồng chỉ kêu khi trăng đỏ');
    expect(text()).toContain('Chờ xác nhận 1');
    expect(text()).toContain('Cảnh 1 · Phố Hội, mưa đêm.');
    await act(async () => button('Xác nhận')!.click());
    await flush();
    expect(calls.find((c) => c.op === 'bible.confirm')?.body).toMatchObject({ ids: [1] });
  });

  it('Câu chuyện: các tab phụ dựng được', async () => {
    for (const tab of ['adapt', 'channel', 'clone']) {
      await render(`/p/3/story?tab=${tab}`, '/p/:projectId/story', <StoryPage />);
      expect(document.querySelector('[role="tab"][aria-selected="true"]')?.id).toBe(`story-tab-${tab}`);
    }
    expect(text()).toContain('Clone cấu trúc một video');
  });

  it('Mẫu: form nền tảng, cài sẵn đã sửa có Khôi phục bản gốc; YAML', async () => {
    await render('/presets', '/presets', <PresetsPage />);
    expect(text()).toContain('cài sẵn · đã sửa');
    expect(button('Khôi phục bản gốc')!.disabled).toBe(false);
    expect((document.getElementById('pf-width') as HTMLInputElement).value).toBe('1080');
    await act(async () => (Array.from(document.querySelectorAll('[role="radio"]')).find((b) => b.textContent === 'YAML') as HTMLElement).click());
    expect((document.getElementById('preset-yaml') as HTMLTextAreaElement).value).toContain('width: 1080');
  });

  it('Lưu trữ: dung lượng theo vùng', async () => {
    await render('/settings/storage', '/settings/storage', <SettingsStorage />);
    expect(text()).toContain('2 KB');
    expect(text()).toContain('Xoá toàn bộ cache');
  });
});
