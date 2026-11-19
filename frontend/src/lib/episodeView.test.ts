import { describe, expect, it } from 'vitest';
import type { Clip, Scene } from './episodeTypes';
import { buildRows, clipView, formatClipSeconds, formatTotalDuration, gridColumns, gridRowHeight } from './episodeView';

const clip = (id: number, scene: number | null, extra: Partial<Clip> = {}): Clip =>
  ({
    id, sequence_id: 1, scene_id: scene, rank: `a${id}`, key: `c${id}`, title: '', image_prompt: '', motion_prompt: '',
    image_prompt_src: {}, motion_prompt_src: {}, prompt_mode: 'synced', assets: [], engine: 'ai_video', duration_ms: 4000,
    link: 'cut', image_take_id: null, video_take_id: null, flags: {}, review: 'pending', notes: '', shot_size: '',
    angle: '', camera_move: '', lens: '', composition: '', eyeline: '', screen_dir: '', custom: {}, updated_at: '',
    ...extra,
  }) as Clip;

describe('buildRows', () => {
  const scenes = new Map<number, Scene>([[10, { id: 10, sequence_id: 1, key: 's01', rank: 'a', title: 'Miếu hoang', summary: '' }]]);
  const list = [clip(1, 10), clip(2, 10), clip(3, 10), clip(4, null), clip(5, 10)];

  it('tiêu đề cảnh mỗi khi cảnh đổi + chia hàng theo số cột', () => {
    const rows = buildRows(list, scenes, 2);
    expect(rows.map((r) => (r.kind === 'scene' ? `H:${r.title}:${r.count}` : r.clips.map((c) => c.id).join(',')))).toEqual([
      'H:Miếu hoang:3', '1,2', '3', 'H:Chưa thuộc cảnh:1', '4', 'H:Miếu hoang:1', '5',
    ]);
    const first = rows[0];
    expect(first.kind === 'scene' && first.durationMs).toBe(12000);
    expect(rows[2].firstIndex).toBe(2);
    // khoá hàng duy nhất kể cả khi cùng cảnh xuất hiện hai lần
    expect(new Set(rows.map((r) => r.key)).size).toBe(rows.length);
  });

  it('bảng: một clip mỗi hàng; có thể bỏ tiêu đề', () => {
    expect(buildRows(list, scenes, 1, false).map((r) => r.firstIndex)).toEqual([0, 1, 2, 3, 4]);
  });

  it('1.000 clip dựng nhanh', () => {
    const many = Array.from({ length: 1000 }, (_, i) => clip(i + 1, Math.floor(i / 100)));
    const t = performance.now();
    const rows = buildRows(many, new Map(), 6);
    expect(performance.now() - t).toBeLessThan(50);
    expect(rows.filter((r) => r.kind === 'scene')).toHaveLength(10);
  });
});

describe('clipView', () => {
  it('việc đang chạy thắng mọi cờ', () => {
    const v = clipView(clip(1, null, { flags: { stale_image: true } }), 'image', { id: 9, status: 'running', progress: 46.4 });
    expect(v.status).toBe('running');
    expect(v.label).toBe('Đang tạo 46%');
    expect(clipView(clip(1, null), 'image', { id: 9, status: 'queued', progress: null, position: 12 }).label).toBe('Chờ #12');
  });
  it('lỗi > cũ > lệch > đã chọn > xong', () => {
    expect(clipView(clip(1, null, { flags: { failed: { part: 'image', message: 'MaiVideo trả 502' } } }), 'image').error).toBe('MaiVideo trả 502');
    expect(clipView(clip(1, null, { flags: { failed: { part: 'video' } } }), 'image').status).toBe('empty');
    expect(clipView(clip(1, null, { image_take_id: 3, flags: { stale_image: true, drift: true } }), 'image').status).toBe('stale');
    expect(clipView(clip(1, null, { image_take_id: 3, flags: { drift: { score: 62 } } }), 'image').status).toBe('drifted');
    expect(clipView(clip(1, null, { image_take_id: 3, review: 'approved' }), 'image').status).toBe('approved');
    expect(clipView(clip(1, null, { image_take_id: 3 }), 'image').status).toBe('done');
    expect(clipView(clip(1, null, { image_take_id: 3 }), 'video').status).toBe('empty');
  });
});

describe('định dạng + lưới', () => {
  it('thời lượng kiểu vi-VN', () => {
    expect(formatClipSeconds(4000)).toBe('4,0 s');
    expect(formatTotalDuration(372000)).toBe('6 ph 12 s');
    expect(formatTotalDuration(48000)).toBe('48 s');
  });
  it('số cột và chiều cao hàng', () => {
    expect(gridColumns(375, '9:16')).toBe(2);
    expect(gridColumns(880, '9:16')).toBe(6);
    expect(gridColumns(880, '16:9')).toBe(3);
    const h = gridRowHeight(880, 6, '9:16');
    expect(h).toBe(Math.round(((880 - 60) / 6) * (16 / 9) + 52 + 12));
  });
});
