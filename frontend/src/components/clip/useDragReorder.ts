/**
 * Kéo thả đổi thứ tự clip bằng tay nắm (pointer capture), dùng chung cho lưới và bảng ảo hoá.
 *
 * - Bắt đầu sau 4px (DESIGN §7.4); chỉ nút chính; theo đúng một `pointerId`; `Esc` huỷ.
 * - Đích thả tìm từ phần tử dưới con trỏ có `data-clip-index` (so trung điểm theo trục `axis`) hoặc tiêu đề cảnh
 *   `data-scene-first` (thả vào đầu cảnh đó). Không đổi DOM khi đang kéo — chỉ vẽ vạch accent 2px (`marker`).
 * - Tự cuộn khung khi con trỏ gần mép trên/dưới (rAF, tốc độ theo độ sâu vào vùng mép).
 */
import { useCallback, useEffect, useRef, useState } from 'react';

export interface DropTarget {
  index: number;
  scene?: number | null;
  /** Vạch thả (toạ độ viewport). */
  marker: { x: number; y: number; w: number; h: number };
}

export interface DragState {
  ids: number[];
  target: DropTarget | null;
}

const START_PX = 4;
const EDGE = 64;
const MAX_SPEED = 900; // px/s

export function useDragReorder({
  scrollRef,
  axis,
  onDrop,
  onCancel,
}: {
  scrollRef: React.RefObject<HTMLElement | null>;
  /** 'y' = bảng (trên/dưới), 'x' = lưới (trái/phải trong hàng). */
  axis: 'x' | 'y';
  onDrop: (ids: number[], target: DropTarget) => void;
  onCancel?: () => void;
}) {
  const [drag, setDrag] = useState<DragState | null>(null);
  const st = useRef<{
    pointerId: number;
    x0: number;
    y0: number;
    ids: number[];
    active: boolean;
    lastX: number;
    lastY: number;
    target: DropTarget | null;
    raf: number;
    t: number;
    handle: HTMLElement;
  } | null>(null);

  const hit = useCallback(
    (x: number, y: number): DropTarget | null => {
      const box = scrollRef.current?.getBoundingClientRect();
      if (!box) return null;
      const cx = Math.min(box.right - 2, Math.max(box.left + 2, x));
      const cy = Math.min(box.bottom - 2, Math.max(box.top + 2, y));
      const el = document.elementFromPoint(cx, cy) as HTMLElement | null;
      const head = el?.closest<HTMLElement>('[data-scene-first]');
      if (head) {
        const r = head.getBoundingClientRect();
        const sceneAttr = head.dataset.sceneId;
        return {
          index: Number(head.dataset.sceneFirst),
          scene: sceneAttr === '' || sceneAttr == null ? null : Number(sceneAttr),
          marker: { x: r.left, y: r.bottom - 1, w: r.width, h: 2 },
        };
      }
      const cell = el?.closest<HTMLElement>('[data-clip-index]');
      if (!cell) return null;
      const r = cell.getBoundingClientRect();
      const i = Number(cell.dataset.clipIndex);
      const after = axis === 'y' ? cy > r.top + r.height / 2 : cx > r.left + r.width / 2;
      const marker =
        axis === 'y'
          ? { x: r.left, y: (after ? r.bottom : r.top) - 1, w: r.width, h: 2 }
          : { x: (after ? r.right + 5 : r.left - 7), y: r.top, w: 2, h: r.height };
      return { index: after ? i + 1 : i, marker };
    },
    [axis, scrollRef],
  );

  const stop = useCallback((commit: boolean) => {
    const s = st.current;
    if (!s) return;
    cancelAnimationFrame(s.raf);
    document.documentElement.style.removeProperty('cursor');
    if (s.handle.hasPointerCapture?.(s.pointerId)) s.handle.releasePointerCapture(s.pointerId);
    st.current = null;
    setDrag(null);
    if (s.active && commit && s.target) onDrop(s.ids, s.target);
    else if (s.active && !commit) onCancel?.();
  }, [onDrop, onCancel]);

  const tick = useCallback((now: number) => {
    const s = st.current;
    const el = scrollRef.current;
    if (!s || !el) return;
    const dt = Math.min(0.05, (now - s.t) / 1000);
    s.t = now;
    const box = el.getBoundingClientRect();
    const zone = Math.min(EDGE, box.height / 3);
    let v = 0;
    if (s.lastY < box.top + zone) v = -Math.min(1.5, (box.top + zone - s.lastY) / zone);
    else if (s.lastY > box.bottom - zone) v = Math.min(1.5, (s.lastY - (box.bottom - zone)) / zone);
    if (v !== 0) {
      el.scrollTop += v * MAX_SPEED * dt;
      s.target = hit(s.lastX, s.lastY);
      setDrag({ ids: s.ids, target: s.target });
    }
    s.raf = requestAnimationFrame(tick);
  }, [hit, scrollRef]);

  useEffect(() => {
    const onMove = (e: PointerEvent) => {
      const s = st.current;
      if (!s || e.pointerId !== s.pointerId) return;
      s.lastX = e.clientX;
      s.lastY = e.clientY;
      if (!s.active) {
        if (Math.hypot(e.clientX - s.x0, e.clientY - s.y0) < START_PX) return;
        s.active = true;
        document.documentElement.style.cursor = 'grabbing';
        s.t = performance.now();
        s.raf = requestAnimationFrame(tick);
      }
      s.target = hit(e.clientX, e.clientY);
      setDrag({ ids: s.ids, target: s.target });
    };
    const onUp = (e: PointerEvent) => {
      if (st.current && e.pointerId === st.current.pointerId) stop(e.type === 'pointerup');
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && st.current?.active) {
        e.preventDefault();
        e.stopPropagation();
        stop(false);
      }
    };
    document.addEventListener('pointermove', onMove);
    document.addEventListener('pointerup', onUp);
    document.addEventListener('pointercancel', onUp);
    window.addEventListener('keydown', onKey, true);
    return () => {
      document.removeEventListener('pointermove', onMove);
      document.removeEventListener('pointerup', onUp);
      document.removeEventListener('pointercancel', onUp);
      window.removeEventListener('keydown', onKey, true);
    };
  }, [hit, stop, tick]);

  /** Gắn vào `onPointerDown` của tay nắm. */
  const begin = useCallback((e: React.PointerEvent<HTMLElement>, ids: number[]) => {
    if (e.button !== 0 || st.current) return;
    e.preventDefault();
    const handle = e.currentTarget;
    handle.setPointerCapture?.(e.pointerId);
    st.current = {
      pointerId: e.pointerId, x0: e.clientX, y0: e.clientY, ids, active: false, lastX: e.clientX, lastY: e.clientY,
      target: null, raf: 0, t: 0, handle,
    };
  }, []);

  return { drag, begin };
}
