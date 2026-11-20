/**
 * Hàm thuần cho bảng clip: trạng thái hiển thị của clip, hàng ảo hoá (lưới theo hàng / bảng theo dòng, có tiêu đề
 * cảnh), nhãn enum shot. Danh sách giá trị lấy từ `meta.enums` (server là nguồn sự thật — `enumOptions`); bảng dưới
 * chỉ giữ nhãn tiếng Việt + làm danh sách dự phòng khi `meta.enums` chưa về.
 */
import type { ClipStatus } from '../components/ui/StatusPill';
import type { Clip, Scene, Take } from './episodeTypes';

/* ---------------------------------------------------------------- */
/* Enum shot                                                         */
/* ---------------------------------------------------------------- */

export type ShotField = 'shot_size' | 'angle' | 'camera_move' | 'lens' | 'composition' | 'eyeline' | 'screen_dir';

export const SHOT_FIELD_LABEL: Record<ShotField, string> = {
  shot_size: 'Cỡ cảnh',
  angle: 'Góc máy',
  camera_move: 'Chuyển động máy',
  lens: 'Ống kính',
  composition: 'Bố cục',
  eyeline: 'Hướng nhìn',
  screen_dir: 'Hướng màn hình',
};

/** Giá trị → nhãn (thứ tự = thứ tự trong menu). Mã giữ nguyên trong ngoặc cho người quen ký hiệu nghề. */
export const SHOT_ENUMS: Record<ShotField, [string, string][]> = {
  shot_size: [
    ['ews', 'Toàn cảnh rất rộng (EWS)'], ['ws', 'Toàn cảnh (WS)'], ['fs', 'Toàn thân (FS)'],
    ['mws', 'Trung toàn (MWS)'], ['ms', 'Trung cảnh (MS)'], ['mcu', 'Cận trung (MCU)'], ['cu', 'Cận cảnh (CU)'],
    ['ecu', 'Đặc tả (ECU)'], ['insert', 'Insert'],
  ],
  angle: [
    ['eye', 'Ngang tầm mắt'], ['low', 'Góc thấp'], ['high', 'Góc cao'], ['dutch', 'Nghiêng (dutch)'],
    ['ots', 'Qua vai (OTS)'], ['pov', 'Góc nhìn nhân vật (POV)'], ['overhead', 'Từ trên xuống'],
  ],
  camera_move: [
    ['static', 'Đứng yên'], ['push_in', 'Đẩy vào'], ['pull_out', 'Kéo ra'], ['pan_l', 'Lia trái'], ['pan_r', 'Lia phải'],
    ['tilt_u', 'Ngước lên'], ['tilt_d', 'Cúi xuống'], ['truck_l', 'Trượt trái'], ['truck_r', 'Trượt phải'],
    ['track', 'Bám theo'], ['arc', 'Vòng cung'], ['crane_up', 'Cẩu lên'], ['crane_down', 'Cẩu xuống'],
    ['handheld', 'Cầm tay'], ['whip_pan', 'Lia vụt'], ['rack_focus', 'Chuyển nét'],
  ],
  lens: [['24mm', '24mm'], ['35mm', '35mm'], ['50mm', '50mm'], ['85mm', '85mm'], ['anamorphic', 'Anamorphic']],
  composition: [
    ['center', 'Giữa khung'], ['thirds_l', '1/3 trái'], ['thirds_r', '1/3 phải'], ['symmetry', 'Đối xứng'],
    ['leading_lines', 'Đường dẫn'], ['frame_in_frame', 'Khung trong khung'], ['negative_space', 'Khoảng trống'],
    ['headroom_tight', 'Sát đầu'],
  ],
  eyeline: [
    ['screen_left', 'Sang trái màn'], ['screen_right', 'Sang phải màn'], ['to_camera', 'Nhìn máy'], ['down', 'Nhìn xuống'],
    ['up', 'Nhìn lên'],
  ],
  screen_dir: [['L→R', 'Trái → phải'], ['R→L', 'Phải → trái'], ['toward_cam', 'Về phía máy'], ['away_cam', 'Ra xa máy'], ['none', 'Không']],
};

export const ENGINE_LABEL: Record<string, string> = {
  ai_video: 'Video AI',
  still_motion: 'Ảnh động',
  code_anim: 'Hoạt hoạ code',
};

export const LINK_LABEL: Record<string, string> = { cut: 'Cắt', fade: 'Mờ chuyển', continue: 'Nối tiếp' };

/** Kết quả `meta.enums` (phần UI dùng). */
export interface MetaEnums {
  shot?: Partial<Record<ShotField, string[]>>;
  engines?: string[];
  links?: string[];
  verdicts?: string[];
}

export interface EnumOptions {
  shot: Record<ShotField, [string, string][]>;
  engine: [string, string][];
  link: [string, string][];
}

const SHOT_LABEL_MAP = Object.fromEntries(
  (Object.keys(SHOT_ENUMS) as ShotField[]).map((f) => [f, Object.fromEntries(SHOT_ENUMS[f])]),
) as Record<ShotField, Record<string, string>>;

/** Giá trị theo thứ tự server → `[giá trị, nhãn]`; giá trị lạ (server thêm mới) giữ nguyên làm nhãn. */
export function withLabels(values: string[], labels: Record<string, string>): [string, string][] {
  return values.map((v) => [v, labels[v] ?? v]);
}

/** Lựa chọn của menu enum: theo `meta.enums` nếu có, không thì danh sách dự phòng tĩnh. */
export function enumOptions(meta?: MetaEnums | null): EnumOptions {
  const shot = {} as Record<ShotField, [string, string][]>;
  for (const f of Object.keys(SHOT_ENUMS) as ShotField[]) {
    const vals = meta?.shot?.[f];
    shot[f] = vals?.length ? withLabels(vals, SHOT_LABEL_MAP[f]) : SHOT_ENUMS[f];
  }
  return {
    shot,
    engine: meta?.engines?.length ? withLabels(meta.engines, ENGINE_LABEL) : Object.entries(ENGINE_LABEL),
    link: meta?.links?.length ? withLabels(meta.links, LINK_LABEL) : Object.entries(LINK_LABEL),
  };
}

export const STATIC_ENUMS: EnumOptions = enumOptions(null);

/** Nhãn ngắn của cỡ cảnh trong bảng: "MCU". */
export function shotShort(v: string): string {
  return v ? v.toUpperCase() : '';
}

export function shotLabel(field: ShotField, v: string): string {
  return SHOT_LABEL_MAP[field][v] ?? v;
}

/** "4,0 s" (DESIGN §9 — một chữ số thập phân cho clip). */
export function formatClipSeconds(ms: number): string {
  return `${(ms / 1000).toLocaleString('vi-VN', { minimumFractionDigits: 1, maximumFractionDigits: 1 })} s`;
}

/** "6 ph 12 s" · "48 s". */
export function formatTotalDuration(ms: number): string {
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return r ? `${m} ph ${r} s` : `${m} ph`;
}

/* ---------------------------------------------------------------- */
/* Trạng thái clip                                                   */
/* ---------------------------------------------------------------- */

export interface ActiveJob {
  id: number;
  status: string;
  progress: number | null;
  /** Vị trí trong hàng đợi chung (nếu biết). */
  position?: number | null;
  part?: 'image' | 'video' | null;
}

export type ViewPart = 'image' | 'video';

export interface ClipView {
  status: ClipStatus | 'empty';
  /** Nhãn pill (đã kèm số), vd "Chờ #12", "Đang tạo 46%". */
  label: string;
  take: Take | null;
  progress: number | null;
  /** Lý do lỗi (2 dòng trên card). */
  error: string | null;
  /** Số take của phần này (để ghi "4 take"). */
  takeCount: number;
  /** Agent đã chọn take (vòng khoanh đứt xám). */
  agentPick: string | null;
}

const flag = (c: Clip, k: string) => {
  const v = c.flags?.[k];
  return v !== undefined && v !== null && v !== false && v !== '' && v !== 0;
};

function failedReason(c: Clip, part: ViewPart): string | null {
  const f = c.flags?.failed;
  if (f && typeof f === 'object' && (f as { part?: string }).part === part) {
    const m = (f as { message?: unknown; error?: unknown }).message ?? (f as { error?: unknown }).error;
    return typeof m === 'string' && m ? m : 'Lần tạo gần nhất bị lỗi.';
  }
  if (flag(c, `failed_${part}`)) {
    const v = c.flags[`failed_${part}`];
    return typeof v === 'string' ? v : 'Lần tạo gần nhất bị lỗi.';
  }
  return null;
}

/**
 * Trạng thái hiển thị của một clip cho phần `part`. Thứ tự ưu tiên: việc đang chạy/chờ > lỗi > đã cũ > lệch > đã chọn
 * (review approved) > xong (có take) > chưa có gì.
 */
export function clipView(c: Clip, part: ViewPart, job?: ActiveJob | null): ClipView {
  const take = (part === 'image' ? c.selected_takes?.image : c.selected_takes?.video) ?? null;
  const selId = part === 'image' ? c.image_take_id : c.video_take_id;
  const pickedBy = (take?.inputs?.selected_by as string | undefined) ?? null;
  const base: ClipView = {
    status: 'empty', label: 'Chưa tạo', take, progress: null, error: null,
    takeCount: c.take_counts?.[part] ?? 0,
    agentPick: pickedBy && pickedBy.startsWith('agent:') ? pickedBy.slice(6) : null,
  };
  if (job && (job.status === 'running' || job.status === 'queued' || job.status === 'paused')) {
    if (job.status === 'running') {
      const p = job.progress != null ? Math.round(job.progress) : null;
      return { ...base, status: 'running', progress: p, label: p != null ? `Đang tạo ${p}%` : 'Đang tạo' };
    }
    return { ...base, status: 'queued', label: job.position ? `Chờ #${job.position}` : 'Chờ' };
  }
  const err = failedReason(c, part);
  if (err) return { ...base, status: 'failed', label: 'Lỗi', error: err };
  if (flag(c, part === 'image' ? 'stale_image' : 'stale_video')) return { ...base, status: 'stale', label: 'Đã cũ' };
  if (flag(c, 'drift')) return { ...base, status: 'drifted', label: 'Lệch nhất quán' };
  if (selId == null) return base;
  if (c.review === 'approved') return { ...base, status: 'approved', label: 'Đã chọn' };
  return { ...base, status: 'done', label: base.agentPick ? 'Xong · chờ bạn' : 'Xong' };
}

/** Clip có cờ cần xử lý (lỗi/cũ/lệch) — cho `Shift J/K` và bộ lọc. */
export function isFlagged(c: Clip): boolean {
  return Object.entries(c.flags ?? {}).some(([, v]) => v !== undefined && v !== null && v !== false && v !== '' && v !== 0);
}

/* ---------------------------------------------------------------- */
/* Hàng ảo hoá                                                       */
/* ---------------------------------------------------------------- */

export type BoardRow =
  | { kind: 'scene'; key: string; sceneId: number | null; title: string; count: number; firstIndex: number; durationMs: number }
  | { kind: 'clips'; key: string; sceneId: number | null; clips: Clip[]; firstIndex: number };

/**
 * Gom clip (đã theo thứ tự) thành hàng: tiêu đề cảnh mỗi khi `scene_id` đổi giữa hai clip liền nhau, rồi các hàng
 * `perRow` clip (lưới) — bảng dùng `perRow = 1`. `firstIndex` = chỉ số của clip đầu hàng trong danh sách đầy đủ.
 */
export function buildRows(clips: Clip[], scenes: Map<number, Scene>, perRow: number, withHeaders = true): BoardRow[] {
  const rows: BoardRow[] = [];
  const n = Math.max(1, Math.floor(perRow));
  let i = 0;
  let groupNo = 0;
  while (i < clips.length) {
    const sid = clips[i].scene_id;
    let j = i;
    let dur = 0;
    while (j < clips.length && clips[j].scene_id === sid) {
      dur += clips[j].duration_ms;
      j += 1;
    }
    groupNo += 1;
    if (withHeaders) {
      const sc = sid != null ? scenes.get(sid) : undefined;
      rows.push({
        kind: 'scene', key: `s${groupNo}:${sid ?? 'none'}`, sceneId: sid, count: j - i, firstIndex: i, durationMs: dur,
        title: sc ? sc.title || `Cảnh ${sc.key}` : sid == null ? 'Chưa thuộc cảnh' : `Cảnh #${sid}`,
      });
    }
    for (let k = i; k < j; k += n) {
      const part = clips.slice(k, Math.min(k + n, j));
      rows.push({ kind: 'clips', key: `r${part[0].id}`, sceneId: sid, clips: part, firstIndex: k });
    }
    i = j;
  }
  return rows;
}

/** Số cột lưới theo bề rộng (DESIGN §4.6): 9:16 `minmax(132px,1fr)` gap 12; < 480px: 2 cột. */
export function gridColumns(width: number, aspect: string): number {
  if (width <= 0) return 2;
  const portrait = aspect !== '16:9';
  if (width < 480) return portrait ? 2 : 1;
  const min = portrait ? 132 : 220;
  const gap = 12;
  return Math.max(1, Math.floor((width + gap) / (min + gap)));
}

/** Chiều cao một hàng lưới (cố định → không nhảy bố cục khi cuộn). */
export function gridRowHeight(width: number, cols: number, aspect: string, gap = 12, meta = 52): number {
  const cardW = (width - gap * (cols - 1)) / cols;
  const ratio = aspect === '16:9' ? 9 / 16 : aspect === '1:1' ? 1 : 16 / 9;
  return Math.round(cardW * ratio + meta + gap);
}
