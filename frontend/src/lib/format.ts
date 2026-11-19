/** Định dạng số và thời gian theo vi-VN (DESIGN §8). */

const countFmt = new Intl.NumberFormat('vi-VN');

/** 1.234 */
export function formatCount(n: number): string {
  return countFmt.format(n);
}

/** "còn ~2 phút" · "còn ~1 giờ 10 phút" · "còn hơn 1 ngày" · "đang ước tính" (làm tròn lên). */
export function formatEta(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return 'đang ước tính';
  if (seconds > 24 * 3600) return 'còn hơn 1 ngày';
  const minutes = Math.max(1, Math.ceil(seconds / 60));
  if (minutes < 60) return `còn ~${minutes} phút`;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return m ? `còn ~${h} giờ ${m} phút` : `còn ~${h} giờ`;
}

/** "96 lượt" */
export function formatCredits(n: number): string {
  return `${formatCount(n)} lượt`;
}

/** "≈ 18.000 ₫" (ký hiệu ₫ đứng sau, có khoảng trắng). */
export function formatVnd(n: number): string {
  return `≈ ${formatCount(Math.round(n))} ₫`;
}

/** "Còn 14 giây" kiểu đếm ngược ngắn; "1 phút 05 giây" khi ≥ 60 s. */
export function formatSeconds(total: number): string {
  const s = Math.max(0, Math.ceil(total));
  if (s < 60) return `${s} giây`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return r ? `${m} phút ${String(r).padStart(2, '0')} giây` : `${m} phút`;
}

/** Bỏ dấu tiếng Việt để tìm kiếm ("tao lai" khớp "Tạo lại"). */
export function foldVi(s: string): string {
  return s
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/đ/g, 'd')
    .replace(/Đ/g, 'D')
    .toLowerCase();
}
