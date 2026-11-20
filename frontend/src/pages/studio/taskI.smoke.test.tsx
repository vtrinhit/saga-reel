/** Smoke Task I: Studio, Âm thanh, Xuất dựng được với dữ liệu giả (axios adapter trả theo tên op). */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { AxiosAdapter } from 'axios';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { api } from '../../api/client';
import { AudioPage } from '../audio/AudioPage';
import { ExportPage } from '../export/ExportPage';
import { StudioPage } from './StudioPage';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const clip = (id: number, dur: number, extra: Record<string, unknown> = {}) => ({
  id,
  sequence_id: 3,
  key: `c000${id}`,
  title: '',
  scene_id: id < 3 ? 1 : 2,
  duration_ms: dur,
  link: 'cut',
  transition: {},
  engine: 'still_motion',
  image_take_id: null,
  video_take_id: null,
  flags: {},
  review: 'pending',
  shot_size: 'ms',
  updated_at: '2026-11-20T00:00:00Z',
  ...extra,
});

const OPS: Record<string, unknown> = {
  'project.get': { id: 3, key: 'p', name: 'Kiếp Hồ Ly', aspect: '9:16', language: 'vi', languages: ['vi', 'en'], target_platforms: ['tiktok'], settings: {} },
  'sequence.get': { id: 3, project_id: 3, number: 1, title: 'Đêm trăng máu', script: 'CẢNH 1', gates: {}, pipeline: { run: { workflow: 'full_auto', status: 'waiting_review', waiting: { gate: 'animatic' }, steps: [] }, memory: {} }, counts: { clips: 3 }, settings: {} },
  'sequence.list': { items: [{ id: 3, project_id: 3, number: 1, title: 'Đêm trăng máu', target_platforms: [] }], next_cursor: null },
  'clip.list': { items: [clip(1, 4000), clip(2, 3000, { link: 'fade' }), clip(3, 5000, { transition: { name: 'fadeblack' } })], next_cursor: null },
  'dialogue.list': {
    items: [
      { id: 10, clip_id: 1, text: 'Đêm ấy, trăng ngả màu máu.', offset_ms: 0, subtitle: true, source: 'tts', speaker_asset_id: null, translations: {} },
      { id: 11, clip_id: 2, text: 'Đừng gõ chuông ấy.', offset_ms: 200, subtitle: true, source: 'tts', speaker_asset_id: 5, audio_take_id: 4, audio_media_id: 41, audio_duration_ms: 1800, translations: { en: "Don't ring it." } },
    ],
  },
  'timeline.list': { items: [{ id: 20, sequence_id: 3, track: 'music', anchor_clip_id: 1, offset_ms: 0, end_anchor: null, duration_ms: null, media_id: 9, sound_id: 1, params: { volume_db: -18 } }] },
  'render.list': { items: [] },
  'preset.list': { items: [
    { id: 1, kind: 'platform', key: 'tiktok', name: 'TikTok', data: { aspect: '9:16', width: 1080, height: 1920, duration_s: { max: 180 }, safe_zone_px: { top: 160, bottom: 480, left: 60, right: 140 }, safe_zone_ref: { width: 1080, height: 1920 }, caption: { max_utf16: 2200 }, hashtags: { max: 5 } } },
  ] },
  'sound.list': { items: [{ id: 1, project_id: 3, media_id: 9, kind: 'music', title: 'Sáo trúc đêm', tags: [], source: 'tự thu', license: 'CC BY 4.0', attribution: '' }] },
  'voice.list': { items: [{ id: 2, project_id: 3, key: 'v', name: 'HoaiMy ấm', connection: '', voice: 'vi-VN-HoaiMyNeural', rate: '', pitch: '', volume: '', style: '', per_lang: {} }] },
  'asset.list': { items: [{ id: 5, key: 'an', name: 'Lạc An', kind: 'character', voice_profile_id: 2 }] },
  'job.list': { items: [{ id: 99, type: 'audio.tts', status: 'running', params: { dialogue_id: 10, lang: 'vi' } }], next_cursor: null },
  'audio.generate': { estimate: { lines: 1, cached: 0 } },
  'audio.subtitle_preview': { cues: [{ start_ms: 0, end_ms: 1000, text: 'Đêm ấy,', warnings: [] }] },
  'export.list': { items: [] },
  'export.check': { errors: [], warnings: [{ code: 'no_final_render', message: 'Tập chưa có bản dựng cuối' }], info: { duration_ms: 12000, size: 40_000_000, estimated: true }, metadata: { title: 'Đêm trăng máu', description: 'Lạc An gõ chuông', hashtags: ['Kiếp Hồ Ly', 'series'], source: 'template' } },
  'auth.status': { authenticated: true, role: 'owner', local: true },
};

const adapter: AxiosAdapter = async (config) => {
  const url = config.url ?? '';
  const name = url.replace('/api/ops/', '');
  const body = typeof config.data === 'string' ? JSON.parse(config.data) : (config.data ?? {});
  let data = url.startsWith('/api/auth') ? OPS['auth.status'] : (OPS[name] ?? {});
  if (name === 'preset.list') data = body.kind === 'platform' ? OPS['preset.list'] : { items: [] }; // transition rỗng → bảng mặc định
  return { data, status: 200, statusText: 'OK', headers: {}, config, request: {} };
};

let container: HTMLDivElement;
let root: Root;
const original = api.defaults.adapter;

beforeEach(() => {
  api.defaults.adapter = adapter;
  // Bố cục máy tính 1920 (jsdom không có matchMedia).
  window.matchMedia = ((q: string) => ({
    matches: /min-width/.test(q),
    media: q,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  })) as unknown as typeof window.matchMedia;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  api.defaults.adapter = original;
});

async function mount(path: string, pattern: string, element: React.ReactElement) {
  const router = createMemoryRouter([{ path: pattern, element }], { initialEntries: [path] });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(
      <QueryClientProvider client={qc}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );
  });
  for (let i = 0; i < 4; i += 1) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 25));
    });
  }
}

describe('Task I (smoke)', () => {
  it('Studio: timeline 5 track theo layout, gate chờ, safe-zone TikTok, inspector tập', async () => {
    await mount('/p/3/studio/3', '/p/:projectId/studio/:seqId', <StudioPage />);
    const t = container.textContent ?? '';
    expect(t).toContain('Storyboard');
    expect(t).toContain('Gate animatic chờ bạn');
    expect(t).toContain('Duyệt animatic');
    expect(t).toContain('Xem trước 30 s');
    // 3 clip, fade 500 (cùng cảnh) + fadeblack sang cảnh mới 1000 → tổng 4000+3000+5000−500−1000 = 10500 ms.
    expect(container.querySelector('[role="region"][aria-label^="Timeline"]')?.getAttribute('aria-label')).toContain('00:10');
    expect(container.querySelectorAll('button[aria-label^="c000"]').length).toBe(3);
    expect(container.querySelector('button[aria-label^="c0003"]')?.getAttribute('aria-label')).toContain('fadeblack');
    expect(t).toContain('TikTok · vùng an toàn');
    expect(t).toContain('Sáo trúc đêm');
  });

  it('Studio: chọn clip → inspector clip', async () => {
    await mount('/p/3/studio/3', '/p/:projectId/studio/:seqId', <StudioPage />);
    const b = container.querySelector<HTMLButtonElement>('button[aria-label^="c0002"]')!;
    await act(async () => b.click());
    const t = container.textContent ?? '';
    expect(b.getAttribute('aria-pressed')).toBe('true');
    expect(t).toContain('Chuyển cảnh từ c0001');
    expect(t).toContain('Đang chồng 0,5 s');
    expect(t).toContain('Tạo lại video');
  });

  it('Âm thanh: thoại theo clip, trạng thái đọc, giọng theo nhân vật, kho nhạc', async () => {
    await mount('/p/3/s/3/audio', '/p/:projectId/s/:seqId/audio', <AudioPage />);
    const t = container.textContent ?? '';
    expect(t).toContain('Thoại theo clip');
    expect(t).toContain('Cảnh 1 · c0001');
    expect(t).toContain('Đang đọc');
    expect(t).toContain('Lạc An');
    expect(t).toContain('Người kể');
    expect(t).toContain('Đọc 1 câu còn lại');
    expect(t).toContain('Sáo trúc đêm');
    expect(t).toContain('English');
  });

  it('Xuất: ma trận nền tảng × ngôn ngữ, thiếu dịch, metadata, gói trống', async () => {
    await mount('/p/3/export?seq=3', '/p/:projectId/export', <ExportPage />);
    const t = container.textContent ?? '';
    expect(t).toContain('Xuất gói · Tập 1');
    expect(t).toContain('TikTok');
    expect(t).toContain('Tiếng Việt');
    expect(t).toContain('Đạt');
    expect(t).toContain('Thiếu dịch');
    expect(t).toContain('1 câu thoại chưa dịch');
    expect(t).toContain('Tập chưa có bản dựng cuối');
    expect(t).toContain('Chưa có gói nào');
    expect(container.querySelector('[role="region"][aria-label="Ma trận nền tảng × ngôn ngữ"]')).not.toBeNull();
    // Metadata điền sẵn bản server sẽ sinh ra (export.check → metadata); hashtag thêm #.
    expect((container.querySelector('#md-desc') as HTMLTextAreaElement | null)?.value).toBe('Lạc An gõ chuông');
    expect((container.querySelector('#md-tags') as HTMLInputElement | null)?.value).toBe('#Kiếp Hồ Ly, #series');
    expect(t).toContain('viết theo mẫu');
    expect(t).toContain('Dùng khi xuất');
  });
});
