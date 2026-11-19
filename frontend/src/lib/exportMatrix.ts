/**
 * Ma trận xuất (tập × nền tảng × ngôn ngữ): khoá ô, tóm tắt kết quả `export.check`, giới hạn metadata theo preset,
 * đơn vị gửi `export.create`. Hàm thuần — test ở `exportMatrix.test.ts`.
 */
import type { CheckReport, ExportRow, Issue, PlatformData } from './studioData';

export type CellKey = `${string}|${string}`;

export const cellKey = (platform: string, lang: string): CellKey => `${platform}|${lang}`;

export function parseCellKey(k: CellKey): { platform: string; lang: string } {
  const [platform, lang] = k.split('|');
  return { platform, lang };
}

export type CellVerdict = 'ok' | 'warn' | 'error' | 'split' | 'unknown';

const SPLIT_CODES = new Set(['duration_will_split']);
/** Cảnh báo chỉ nói "chưa đo được" (chưa có bản cuối) — không tính là vấn đề của ô. */
export const SOFT_CODES = new Set(['no_final_render', 'loudness_not_measured']);

/** Pill của ô: lỗi > chia phần > cảnh báo > đạt. Cảnh báo "chưa có bản cuối" không làm ô thành cảnh báo. */
export function verdict(report: CheckReport | null | undefined): CellVerdict {
  if (!report) return 'unknown';
  if (report.errors.length) return 'error';
  const warnings = report.warnings.filter((w) => !SOFT_CODES.has(w.code));
  if (warnings.some((w) => SPLIT_CODES.has(w.code))) return 'split';
  if (warnings.length) return 'warn';
  return 'ok';
}

export function verdictLabel(v: CellVerdict, report?: CheckReport | null): string {
  switch (v) {
    case 'ok':
      return 'Đạt';
    case 'error':
      return `${report?.errors.length ?? 0} lỗi`;
    case 'split':
      return 'Chia phần';
    case 'warn':
      return `${report?.warnings.filter((w) => !SOFT_CODES.has(w.code)).length ?? 0} cảnh báo`;
    default:
      return 'Chưa kiểm';
  }
}

/** Mọi vấn đề theo thứ tự hiện: lỗi trước, rồi cảnh báo. */
export function issues(report: CheckReport | null | undefined): (Issue & { level: 'error' | 'warn' })[] {
  if (!report) return [];
  return [
    ...report.errors.map((i) => ({ ...i, level: 'error' as const })),
    ...report.warnings.map((i) => ({ ...i, level: 'warn' as const })),
  ];
}

/** Đếm ký tự như nền tảng: UTF-16 nếu preset đo UTF-16, còn lại theo code point. */
export function countChars(text: string, utf16 = false): number {
  return utf16 ? text.length : Array.from(text).length;
}

export function countBytes(text: string): number {
  return new TextEncoder().encode(text).length;
}

export interface MetaLimits {
  titleMax: number | null;
  captionMax: number | null;
  captionUtf16: boolean;
  captionBytes: number | null;
  hashtagMax: number | null;
  aiLabelRequired: boolean;
}

export function metaLimits(p: PlatformData | null | undefined): MetaLimits {
  const utf16 = p?.caption?.max_utf16 ?? null;
  return {
    titleMax: p?.title?.max_chars ?? null,
    captionMax: utf16 ?? p?.caption?.max_chars ?? p?.description?.max_chars ?? null,
    captionUtf16: utf16 != null,
    captionBytes: p?.caption?.max_bytes ?? p?.description?.max_bytes ?? null,
    hashtagMax: p?.hashtags?.max ?? null,
    aiLabelRequired: p?.ai_label?.required ?? true,
  };
}

export function hashtagsOf(text: string): string[] {
  return Array.from(text.matchAll(/#[\p{L}\p{N}_]+/gu), (m) => m[0]);
}

/** "≤ 3 phút" từ `duration_s.max`. */
export function durationLimit(p: PlatformData | null | undefined): string {
  const max = p?.duration_s?.max;
  if (!max) return 'không giới hạn';
  if (max < 60) return `≤ ${max} giây`;
  if (max % 3600 === 0) return `≤ ${max / 3600} giờ`;
  return `≤ ${Math.round(max / 60)} phút`;
}

export function sizeLabel(bytes: number | null | undefined): string {
  if (!bytes) return '—';
  const mb = bytes / 1024 / 1024;
  if (mb >= 1024) return `${(mb / 1024).toFixed(1).replace('.', ',')} GB`;
  return `${Math.max(1, Math.round(mb))} MB`;
}

/** Gói gần nhất cho đúng ô (tập, nền tảng, ngôn ngữ). */
export function latestPackage(rows: ExportRow[] | undefined, seqId: number, platform: string, lang: string): ExportRow | null {
  return (rows ?? []).find((r) => r.platform === platform && r.lang === lang && r.sequence_ids.includes(seqId)) ?? null;
}

/** Gom ô đã chọn thành lời gọi `export.create` tối thiểu: nhóm theo nền tảng → ngôn ngữ (op nhận tích chéo). */
export function createCalls(seqIds: number[], selected: CellKey[]): { sequence_ids: number[]; platforms: string[]; langs: string[] }[] {
  const byPlatform = new Map<string, Set<string>>();
  for (const k of selected) {
    const { platform, lang } = parseCellKey(k);
    if (!byPlatform.has(platform)) byPlatform.set(platform, new Set());
    byPlatform.get(platform)!.add(lang);
  }
  // Nền tảng có cùng tập ngôn ngữ thì gộp chung một lời gọi.
  const groups = new Map<string, string[]>();
  for (const [platform, langs] of byPlatform) {
    const sig = [...langs].sort().join(',');
    groups.set(sig, [...(groups.get(sig) ?? []), platform]);
  }
  return [...groups.entries()].map(([sig, platforms]) => ({
    sequence_ids: seqIds,
    platforms: platforms.sort(),
    langs: sig.split(','),
  }));
}

export const LANG_LABEL: Record<string, string> = {
  vi: 'Tiếng Việt',
  en: 'English',
  zh: '中文',
  ja: '日本語',
  ko: '한국어',
  th: 'ไทย',
  id: 'Bahasa Indonesia',
};

export const langLabel = (l: string) => LANG_LABEL[l] ?? l;

export const PACKAGE_STATUS: Record<string, string> = {
  pending: 'Chờ',
  running: 'Đang dựng',
  done: 'Xong',
  succeeded: 'Xong',
  failed: 'Lỗi',
  cancelled: 'Đã huỷ',
  superseded: 'Đã thay',
};
