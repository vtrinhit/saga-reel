/**
 * Thứ tự clip khi kéo thả / `Alt ↑↓` (hàm thuần). Rank là việc của server (`clip.move` chỉ ghi rank của clip được
 * dời); ở đây chỉ tính tham số gửi đi và thứ tự lạc quan để vẽ ngay.
 *
 * Mã hoá của `clip.move`: `after_clip_id` 0 = đầu tập, id = sau clip đó; `scene_id` bỏ trống = giữ cảnh, 0 = bỏ khỏi
 * cảnh, id = chuyển vào cảnh đó.
 */

export interface OrderItem {
  id: number;
  sceneId: number | null;
}

export interface MovePlan {
  /** 0 = đầu tập; còn lại = id clip đứng ngay trước khối được dời. */
  afterClipId: number;
  /** undefined = giữ cảnh; 0 = bỏ khỏi cảnh; id = cảnh mới. */
  sceneId?: number;
  /** id các clip được dời, theo thứ tự hiện tại trong tập (giữ thứ tự tương đối khi chọn rời rạc). */
  ids: number[];
  /** Thứ tự mới của cả tập (để cập nhật lạc quan). */
  order: number[];
  /** Cảnh sau khi dời của từng clip được dời. */
  scenes: Map<number, number | null>;
}

/**
 * Kế hoạch dời các clip `moving` tới chỗ thả `dropIndex` (0…n, tính trên danh sách ĐẦY ĐỦ hiện tại: thả trước phần tử
 * thứ `dropIndex`). `targetScene`: cảnh đích do UI biết chắc (thả ngay dưới tiêu đề cảnh); bỏ trống = cảnh của clip
 * đứng trước chỗ thả (hoặc clip đứng sau nếu thả ở đầu). Trả null khi không có gì đổi.
 */
export function planMove(
  items: OrderItem[],
  moving: Iterable<number>,
  dropIndex: number,
  targetScene?: number | null,
): MovePlan | null {
  const movingSet = new Set(moving);
  const block = items.filter((it) => movingSet.has(it.id));
  if (block.length === 0) return null;
  const clamped = Math.max(0, Math.min(items.length, Math.trunc(dropIndex)));
  let restPos = 0;
  for (let i = 0; i < clamped; i++) if (!movingSet.has(items[i].id)) restPos += 1;
  return planAt(items, block, movingSet, restPos, targetScene);
}

/**
 * `Alt ↑` / `Alt ↓`: dời khối đang chọn lên/xuống một bậc (qua đúng một clip không được chọn). Ở biên → null.
 */
export function planStep(items: OrderItem[], moving: Iterable<number>, dir: -1 | 1): MovePlan | null {
  const movingSet = new Set(moving);
  const block = items.filter((it) => movingSet.has(it.id));
  if (block.length === 0) return null;
  const rest = items.filter((it) => !movingSet.has(it.id));
  const firstIdx = items.findIndex((it) => movingSet.has(it.id));
  let restPos = 0;
  for (let i = 0; i < firstIdx; i++) if (!movingSet.has(items[i].id)) restPos += 1;
  const next = restPos + dir;
  if (next < 0 || next > rest.length) return null;
  return planAt(items, block, movingSet, next, undefined);
}

function planAt(
  items: OrderItem[],
  block: OrderItem[],
  movingSet: Set<number>,
  restPos: number,
  targetScene: number | null | undefined,
): MovePlan | null {
  const rest = items.filter((it) => !movingSet.has(it.id));
  const before = rest[restPos - 1] ?? null;
  const after = rest[restPos] ?? null;
  const scene: number | null =
    targetScene !== undefined ? targetScene : before ? before.sceneId : after ? after.sceneId : block[0].sceneId;
  const order = [...rest.slice(0, restPos).map((r) => r.id), ...block.map((b) => b.id), ...rest.slice(restPos).map((r) => r.id)];
  const sameOrder = order.every((id, i) => id === items[i].id);
  const sceneChanges = block.some((b) => b.sceneId !== scene);
  if (sameOrder && !sceneChanges) return null;
  const scenes = new Map<number, number | null>(block.map((b) => [b.id, scene]));
  return {
    afterClipId: before ? before.id : 0,
    sceneId: sceneChanges ? (scene ?? 0) : undefined,
    ids: block.map((b) => b.id),
    order,
    scenes,
  };
}

/** Áp kế hoạch lên một mảng bản ghi (lạc quan): sắp theo `plan.order`, đổi `scene_id` của clip được dời. */
export function applyPlan<T extends { id: number; scene_id: number | null }>(list: T[], plan: MovePlan): T[] {
  const byId = new Map(list.map((x) => [x.id, x]));
  const out: T[] = [];
  for (const id of plan.order) {
    const x = byId.get(id);
    if (!x) continue;
    out.push(plan.scenes.has(id) ? { ...x, scene_id: plan.scenes.get(id) ?? null } : x);
  }
  return out;
}

/** Mô tả cho vùng `role=status` sau khi dời: "Clip c0012 sang vị trí 5 / 48". */
export function moveAnnouncement(plan: MovePlan, keyOf: (id: number) => string): string {
  const pos = plan.order.indexOf(plan.ids[0]) + 1;
  const what = plan.ids.length === 1 ? `Clip ${keyOf(plan.ids[0])}` : `${plan.ids.length} clip`;
  return `${what} sang vị trí ${pos} / ${plan.order.length}`;
}
