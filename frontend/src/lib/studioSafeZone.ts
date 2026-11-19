/**
 * Lớp safe-zone trên player: dải che UI của nền tảng (theo `platform.safe_zone_px` + `safe_zone_ref` của preset), quy
 * ra phần trăm khung để vẽ bằng CSS (không phụ thuộc kích thước player). Preset không có safe zone → title-safe 90%.
 */
import type { PlatformData } from './studioData';

export interface SafeInsets {
  /** Phần trăm (0–100) của chiều cao/rộng khung. */
  top: number;
  bottom: number;
  left: number;
  right: number;
  /** true = vùng an toàn tiêu đề 90% (không có số liệu của nền tảng). */
  titleSafe: boolean;
}

const pct = (v: number | undefined, ref: number) => (v && ref > 0 ? Math.max(0, Math.min(50, (v / ref) * 100)) : 0);

export function safeInsets(data: PlatformData | null | undefined): SafeInsets {
  const px = data?.safe_zone_px;
  const refW = data?.safe_zone_ref?.width ?? data?.width ?? 0;
  const refH = data?.safe_zone_ref?.height ?? data?.height ?? 0;
  if (!px || !refW || !refH) return { top: 5, bottom: 5, left: 5, right: 5, titleSafe: true };
  return {
    top: pct(px.top, refH),
    bottom: pct(px.bottom, refH),
    left: pct(px.left, refW),
    right: pct(px.right, refW),
    titleSafe: false,
  };
}

/** "9:16" → 0.5625; lỗi → 9/16. */
export function aspectRatio(aspect: string | null | undefined): number {
  const m = /^\s*(\d+)\s*[:x/]\s*(\d+)\s*$/.exec(aspect ?? '');
  if (!m || !Number(m[1]) || !Number(m[2])) return 9 / 16;
  return Number(m[1]) / Number(m[2]);
}

/** Preset nền tảng hợp tỉ lệ khung của dự án lên đầu. */
export function platformsForAspect<T extends { key: string; data: PlatformData }>(rows: T[], aspect: string): T[] {
  const want = aspectRatio(aspect) <= 1;
  return [...rows].sort((a, b) => {
    const fa = aspectRatio(a.data.aspect) <= 1 === want ? 0 : 1;
    const fb = aspectRatio(b.data.aspect) <= 1 === want ? 0 : 1;
    return fa - fb || a.key.localeCompare(b.key);
  });
}
