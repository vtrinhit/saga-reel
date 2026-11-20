import { describe, expect, it } from 'vitest';
import { costBuckets } from '../components/pipeline/ProductionMatrix';
import { derivedFromTakes, MAX_PATCH, mergePatch, planLivePatch } from './episodePatch';
import type { Clip, Take } from './episodeTypes';
import { changeSetOf } from './episodeUndo';
import { enumOptions, shotLabel, STATIC_ENUMS } from './episodeView';
import { jobStatusView } from './jobText';

const clip = (id: number, extra: Partial<Clip> = {}): Clip =>
  ({ id, sequence_id: 1, scene_id: null, rank: `a${id}`, key: `c${id}`, image_prompt: '', review: 'pending', flags: {}, ...extra }) as Clip;

describe('planLivePatch', () => {
  it('không có thực thể → nạp lại; cảnh đổi → nạp lại; chỉ take/thoại → không vá clip', () => {
    expect(planLivePatch(null)).toEqual({ kind: 'full' });
    expect(planLivePatch([['clip', 1], ['scene', 3]])).toEqual({ kind: 'full' });
    expect(planLivePatch([['dialogue_line', 9]])).toEqual({ kind: 'none' });
  });
  it('vài clip → vá đúng các id (gộp trùng); quá ngưỡng → nạp lại', () => {
    expect(planLivePatch([['clip', 2], ['clip', 2], ['take', 7], ['clip', 5]])).toEqual({ kind: 'patch', ids: [2, 5] });
    const many = Array.from({ length: MAX_PATCH + 1 }, (_, i) => ['clip', i] as [string, number]);
    expect(planLivePatch(many)).toEqual({ kind: 'full' });
  });
});

describe('mergePatch', () => {
  const list = [clip(1), clip(2), clip(3)];
  it('thay tại chỗ, giữ object clip không đổi', () => {
    const next = mergePatch(list, 1, [{ id: 2, clip: clip(2, { image_prompt: 'mới' }) }])!;
    expect(next.map((c) => c.id)).toEqual([1, 2, 3]);
    expect(next[1].image_prompt).toBe('mới');
    expect(next[0]).toBe(list[0]);
  });
  it('xoá (404 hoặc deleted_at) → bỏ khỏi danh sách', () => {
    expect(mergePatch(list, 1, [{ id: 3, deleted: true }])!.map((c) => c.id)).toEqual([1, 2]);
    expect(mergePatch(list, 1, [{ id: 1, clip: clip(1, { deleted_at: '2026-11-20' }) }])!.map((c) => c.id)).toEqual([2, 3]);
  });
  it('dời / đổi cảnh / clip mới của tập / lỗi đọc → null (nạp lại thứ tự)', () => {
    expect(mergePatch(list, 1, [{ id: 2, clip: clip(2, { rank: 'z' }) }])).toBeNull();
    expect(mergePatch(list, 1, [{ id: 2, clip: clip(2, { scene_id: 4 }) }])).toBeNull();
    expect(mergePatch(list, 1, [{ id: 9, clip: clip(9) }])).toBeNull();
    expect(mergePatch(list, 1, [{ id: 2, unknown: true }])).toBeNull();
  });
  it('clip của tập khác → bỏ qua, danh sách giữ nguyên', () => {
    expect(mergePatch(list, 1, [{ id: 9, clip: clip(9, { sequence_id: 2 }) }])).toBe(list);
  });
});

describe('derivedFromTakes', () => {
  it('take đang chọn + số take + media giống review.queue', () => {
    const t = (id: number, part: 'image' | 'video', media_id: number) => ({ id, part, media_id }) as unknown as Take;
    const d = derivedFromTakes(clip(1, { image_take_id: 11, video_take_id: null }), {
      items: [t(12, 'image', 5), t(11, 'image', 4), t(20, 'video', 9)],
      selected: { image: 11, video: null },
    });
    expect(d.selected_takes?.image?.id).toBe(11);
    expect(d.selected_takes?.video).toBeNull();
    expect(d.take_counts).toEqual({ image: 2, video: 1 });
    expect(d.image_media_id).toBe(4);
  });
});

describe('BF: hoàn tác theo change set, ngưỡng hỏi lại, enum, hàng đợi, chi phí theo lô', () => {
  it('changeSetOf', () => {
    expect(changeSetOf({ change_set_id: 42 })).toBe(42);
    expect(changeSetOf({ change_set_id: null })).toBeNull();
    expect(changeSetOf(null)).toBeNull();
  });
  it('enumOptions theo meta.enums: thứ tự server, nhãn Việt, giá trị lạ giữ nguyên', () => {
    const o = enumOptions({ shot: { shot_size: ['cu', 'ws', 'xyz'] }, engines: ['still_motion'] });
    expect(o.shot.shot_size).toEqual([['cu', 'Cận cảnh (CU)'], ['ws', 'Toàn cảnh (WS)'], ['xyz', 'xyz']]);
    expect(o.shot.angle).toEqual(STATIC_ENUMS.shot.angle);
    expect(o.engine).toEqual([['still_motion', 'Ảnh động']]);
    expect(shotLabel('lens', 'anamorphic')).toBe('Anamorphic');
  });
  it('jobStatusView: việc chờ kèm vị trí hàng đợi', () => {
    expect(jobStatusView({ status: 'queued', progress: null, queue_position: 3 }).label).toBe('Chờ #3');
    expect(jobStatusView({ status: 'queued', progress: null }).label).toBe('Chờ');
  });
  it('costBuckets: nhóm 50 theo vị trí, chỉ nhóm có hàng đang hiện', () => {
    const ids = Array.from({ length: 120 }, (_, i) => i + 1);
    expect(costBuckets(ids, new Set([3, 60])).map((b) => [b[0], b.length])).toEqual([[1, 50], [51, 50]]);
    expect(costBuckets(ids, new Set([110]))).toEqual([ids.slice(100)]);
  });
});
