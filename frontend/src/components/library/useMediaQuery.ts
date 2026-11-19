import { useSyncExternalStore } from 'react';

/** `true` khi media query khớp (cập nhật khi đổi cỡ cửa sổ). Không có `matchMedia` (test) → `fallback`. */
export function useMediaQuery(query: string, fallback = false): boolean {
  return useSyncExternalStore(
    (cb) => {
      const mq = typeof window !== 'undefined' ? window.matchMedia?.(query) : undefined;
      mq?.addEventListener('change', cb);
      return () => mq?.removeEventListener('change', cb);
    },
    () => (typeof window !== 'undefined' && window.matchMedia ? window.matchMedia(query).matches : fallback),
    () => fallback,
  );
}

/** Mốc inspector cố định của Thư viện (DESIGN: ≥1280). */
export const XL = '(min-width: 1280px)';
