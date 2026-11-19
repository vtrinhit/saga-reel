import { describe, expect, it } from 'vitest';
import { applyPlan, moveAnnouncement, planMove, planStep, type OrderItem } from './episodeOrder';

// 1..6; cảnh 10 = [1,2,3], cảnh 20 = [4,5,6]
const items: OrderItem[] = [
  { id: 1, sceneId: 10 },
  { id: 2, sceneId: 10 },
  { id: 3, sceneId: 10 },
  { id: 4, sceneId: 20 },
  { id: 5, sceneId: 20 },
  { id: 6, sceneId: 20 },
];

describe('planMove', () => {
  it('dời lên trong cùng cảnh', () => {
    const p = planMove(items, [3], 1)!;
    expect(p.order).toEqual([1, 3, 2, 4, 5, 6]);
    expect(p.afterClipId).toBe(1);
    expect(p.sceneId).toBeUndefined();
  });

  it('dời xuống trong cùng cảnh (chỉ số thả tính trên danh sách đầy đủ)', () => {
    const p = planMove(items, [1], 3)!;
    expect(p.order).toEqual([2, 3, 1, 4, 5, 6]);
    expect(p.afterClipId).toBe(3);
    expect(p.sceneId).toBeUndefined();
  });

  it('dời lên đầu tập → after_clip_id = 0', () => {
    const p = planMove(items, [2], 0)!;
    expect(p.order).toEqual([2, 1, 3, 4, 5, 6]);
    expect(p.afterClipId).toBe(0);
  });

  it('dời xuống cuối tập → sau clip cuối, sang cảnh của clip cuối', () => {
    const p = planMove(items, [2], 6)!;
    expect(p.order).toEqual([1, 3, 4, 5, 6, 2]);
    expect(p.afterClipId).toBe(6);
    expect(p.sceneId).toBe(20);
  });

  it('chọn rời rạc giữ thứ tự tương đối và gom thành khối', () => {
    const p = planMove(items, [5, 2], 1)!;
    expect(p.ids).toEqual([2, 5]);
    expect(p.order).toEqual([1, 2, 5, 3, 4, 6]);
    expect(p.afterClipId).toBe(1);
    expect(p.sceneId).toBe(10); // clip 5 rời cảnh 20 sang 10
  });

  it('thả đúng chỗ cũ → null', () => {
    expect(planMove(items, [3], 3)).toBeNull();
    expect(planMove(items, [3], 2)).toBeNull();
    expect(planMove(items, [1, 2], 0)).toBeNull();
  });

  it('thả qua tiêu đề cảnh đổi scene_id', () => {
    // thả clip 1 ngay dưới tiêu đề cảnh 20 (trước clip 4): clip trước chỗ thả là 3 (cảnh 10), UI báo cảnh đích 20
    const p = planMove(items, [1], 3, 20)!;
    expect(p.order).toEqual([2, 3, 1, 4, 5, 6]);
    expect(p.sceneId).toBe(20);
    expect(p.scenes.get(1)).toBe(20);
  });

  it('chỉ đổi cảnh, không đổi chỗ vẫn là một thay đổi', () => {
    const p = planMove(items, [4], 3, 10)!;
    expect(p.order).toEqual([1, 2, 3, 4, 5, 6]);
    expect(p.sceneId).toBe(10);
  });

  it('bỏ khỏi cảnh → scene_id = 0', () => {
    const p = planMove(items, [2], 0, null)!;
    expect(p.sceneId).toBe(0);
  });

  it('kẹp chỉ số thả và bỏ id lạ', () => {
    expect(planMove(items, [99], 2)).toBeNull();
    expect(planMove(items, [1], 99)!.afterClipId).toBe(6);
    expect(planMove(items, [6], -5)!.afterClipId).toBe(0);
  });
});

describe('planStep (Alt ↑/↓)', () => {
  it('lên/xuống một bậc', () => {
    expect(planStep(items, [3], -1)!.order).toEqual([1, 3, 2, 4, 5, 6]);
    expect(planStep(items, [3], 1)!.order).toEqual([1, 2, 4, 3, 5, 6]);
  });
  it('qua ranh cảnh thì nhận cảnh của clip đứng trước', () => {
    const p = planStep(items, [3], 1)!;
    expect(p.afterClipId).toBe(4);
    expect(p.sceneId).toBe(20);
  });
  it('ở biên trả null', () => {
    expect(planStep(items, [1], -1)).toBeNull();
    expect(planStep(items, [6], 1)).toBeNull();
  });
  it('khối nhiều clip nhảy qua đúng một clip', () => {
    expect(planStep(items, [2, 3], 1)!.order).toEqual([1, 4, 2, 3, 5, 6]);
  });
});

describe('applyPlan + thông báo', () => {
  it('sắp lại bản ghi và đổi cảnh clip được dời', () => {
    const list = items.map((i) => ({ id: i.id, scene_id: i.sceneId, key: `c${i.id}` }));
    const p = planMove(items, [1], 3, 20)!;
    const out = applyPlan(list, p);
    expect(out.map((x) => x.id)).toEqual([2, 3, 1, 4, 5, 6]);
    expect(out[2].scene_id).toBe(20);
    expect(out[0]).toBe(list[1]); // bản ghi không đổi giữ nguyên tham chiếu
    expect(moveAnnouncement(p, (id) => `c${id}`)).toBe('Clip c1 sang vị trí 3 / 6');
  });
});
