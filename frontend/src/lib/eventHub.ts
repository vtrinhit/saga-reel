/**
 * Phân phát sự kiện realtime trong app: shell giữ MỘT kết nối `/api/events` (`useLiveEvents`) rồi phát từng sự kiện
 * ra đây; trang nào cần (`provider.health`, `job.log`, `job.updated`…) đăng ký bằng `useServerEvent`.
 */
import { useEffect, useRef } from 'react';
import type { ServerEvent } from './events';

type Handler = (e: ServerEvent) => void;

const subs = new Map<string, Set<Handler>>();

/** Đăng ký nhận sự kiện theo loại (`'*'` = mọi loại); trả hàm huỷ. */
export function onServerEvent(type: string, fn: Handler): () => void {
  let set = subs.get(type);
  if (!set) {
    set = new Set();
    subs.set(type, set);
  }
  set.add(fn);
  return () => {
    set.delete(fn);
  };
}

export function emitServerEvent(e: ServerEvent): void {
  for (const key of [e.type, '*']) {
    const set = subs.get(key);
    if (!set) continue;
    for (const fn of [...set]) {
      try {
        fn(e);
      } catch (err) {
        console.error('[events] lỗi trong bộ nhận sự kiện', e.type, err);
      }
    }
  }
}

/** Hook: nhận sự kiện `type` trong suốt vòng đời component; handler mới nhất luôn được gọi (không đăng ký lại). */
export function useServerEvent(type: string, handler: Handler): void {
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => onServerEvent(type, (e) => ref.current(e)), [type]);
}
