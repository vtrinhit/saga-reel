/**
 * Trạng thái hiển thị của card clip (màn sản xuất gộp ảnh + video). Card không còn công tắc Ảnh/Video chung cho cả
 * trang: mỗi card tự chọn phần đang hiện (mặc định: video nếu đã có, không thì ảnh) và người dùng lật riêng từng card.
 *
 * Máy trạng thái (thứ tự ưu tiên):
 *   generating — có việc đang chạy/chờ cho clip (bất kể phần nào)
 *   error      — lần tạo gần nhất của phần đang hiện lỗi (hoặc lỗi ảnh khi chưa có ảnh)
 *   video      — đang hiện phần video và đã có take video
 *   image      — đã có take ảnh
 *   empty      — chưa có ảnh: card hiện prompt + nút "Tạo ảnh"
 */
import type { ActiveJob, ViewPart } from './episodeView';
import { clipView, type ClipView } from './episodeView';
import type { Clip } from './episodeTypes';

export type CardKind = 'empty' | 'image' | 'video' | 'generating' | 'error';

export interface CardState {
  kind: CardKind;
  /** Phần đang hiện trên card. */
  part: ViewPart;
  hasImage: boolean;
  hasVideo: boolean;
  /** media id của khung hiện (ảnh, hoặc poster video). */
  mediaId: number | null;
  /** media id video (để phát trong viewer). */
  videoMediaId: number | null;
  /** Trạng thái chi tiết của phần đang hiện (pill, lỗi, tiến độ, số take). */
  view: ClipView;
  /** Hành động chính duy nhất của trạng thái này. */
  primary: { part: 'image' | 'video'; label: string } | null;
}

export function hasImage(c: Clip): boolean {
  return c.image_take_id != null || c.image_media_id != null;
}

export function hasVideo(c: Clip): boolean {
  return c.video_take_id != null || c.video_media_id != null;
}

/** Phần mặc định của card: video nếu đã có, không thì ảnh. */
export function defaultPart(c: Clip): ViewPart {
  return hasVideo(c) ? 'video' : 'image';
}

function mediaOf(c: Clip, part: ViewPart): number | null {
  const t = part === 'image' ? c.selected_takes?.image : c.selected_takes?.video;
  return (part === 'image' ? c.image_media_id : c.video_media_id) ?? t?.media?.id ?? t?.media_id ?? null;
}

export function cardState(c: Clip, wanted: ViewPart | null | undefined, job?: ActiveJob | null): CardState {
  const img = hasImage(c);
  const vid = hasVideo(c);
  // Chỉ hiện video khi đã có video; muốn video mà chưa có → hiện ảnh.
  const part: ViewPart = wanted === 'video' && vid ? 'video' : wanted === 'image' ? 'image' : vid ? 'video' : 'image';
  const activeJob = job && (job.status === 'running' || job.status === 'queued' || job.status === 'paused') ? job : null;
  const view = clipView(c, activeJob?.part ?? part, activeJob);
  const imageMedia = mediaOf(c, 'image');
  const videoMedia = mediaOf(c, 'video');
  const base = {
    part, hasImage: img, hasVideo: vid, view, videoMediaId: videoMedia,
    // Poster của video: thumb của chính file video (server cắt khung), không có thì ảnh.
    mediaId: part === 'video' ? videoMedia ?? imageMedia : imageMedia,
  };
  if (activeJob) return { ...base, kind: 'generating', primary: null };
  const imgView = clipView(c, 'image');
  const partView = clipView(c, part);
  if (partView.status === 'failed' || (!img && imgView.status === 'failed')) {
    const failedPart: ViewPart = partView.status === 'failed' ? part : 'image';
    return {
      ...base,
      view: failedPart === part ? partView : imgView,
      kind: 'error',
      primary: { part: failedPart, label: 'Thử lại' },
    };
  }
  if (!img) return { ...base, view: imgView, kind: 'empty', primary: { part: 'image', label: 'Tạo ảnh' } };
  if (part === 'video') return { ...base, view: partView, kind: 'video', primary: null };
  return { ...base, view: partView, kind: 'image', primary: vid ? null : { part: 'video', label: 'Tạo video' } };
}

/* ---------------------------------------------------------------- */
/* Độ dài hiệu lực (tự cắt)                                          */
/* ---------------------------------------------------------------- */

export interface ClipLength {
  ms: number;
  /** Ghi chú ngắn cạnh số giây: "tự cắt" / "nguyên take" / ''. */
  note: string;
}

/**
 * Độ dài clip trong bản dựng: có video + chế độ tự cắt đã áp dụng → `auto_trim.dur_ms`; "nguyên take" → độ dài take;
 * còn lại (thủ công, chưa có video, chưa phân tích) → `duration_ms`.
 */
export function effectiveLength(c: Clip): ClipLength {
  const mode = c.trim_mode ?? 'auto';
  const at = c.auto_trim;
  if (hasVideo(c) && mode === 'auto' && at?.applied && at.dur_ms != null) return { ms: at.dur_ms, note: 'tự cắt' };
  if (hasVideo(c) && mode === 'none') {
    const take = at?.take_ms ?? c.source_duration_ms;
    if (take) return { ms: take, note: 'nguyên take' };
  }
  return { ms: c.duration_ms, note: '' };
}

/** Tổng độ dài (ms) của các clip theo độ dài hiệu lực. */
export function totalLength(clips: Clip[]): number {
  return clips.reduce((s, c) => s + effectiveLength(c).ms, 0);
}

/* ---------------------------------------------------------------- */
/* Lưới card                                                         */
/* ---------------------------------------------------------------- */

export const CARD_GAP = 12;
export const CARD_GAP_PHONE = 8;
/** Thanh dưới ảnh: số clip · thời lượng · hành động. */
export const CARD_META_H = 44;
/** Một dòng thoại dưới card (chỉ khi hàng có ít nhất một clip có thoại). */
export const CARD_LINE_H = 24;

/**
 * Số cột: ≤ 480 → 2 (16:9: 1) · ~768 → 3 · 1280 → 4–5 · 1920 → 6. Card to hơn lưới cũ (6–10 cột) để ảnh đọc được và
 * nút trên card bấm được; tối đa 6 cột (16:9: 4).
 */
export function cardColumns(width: number, aspect: string): number {
  const portrait = aspect !== '16:9';
  if (width <= 0) return 2;
  if (width < 480) return portrait ? 2 : 1;
  const min = portrait ? 196 : 300;
  const max = portrait ? 6 : 4;
  return Math.max(2, Math.min(max, Math.floor((width + CARD_GAP) / (min + CARD_GAP))));
}

/** Dải tiêu đề cảnh trên mỗi đoạn của hàng. */
export const SEG_HEAD_H = 32;

/** Một đoạn liền của một cảnh trong một hàng lưới. */
export interface RowSegment {
  key: string;
  sceneId: number | null;
  title: string;
  /** Số clip của cả cảnh / tổng độ dài hiệu lực của cả cảnh. */
  count: number;
  durationMs: number;
  /** Chỉ số (trong danh sách đầy đủ) của clip đầu đoạn. */
  firstIndex: number;
  /** Đoạn bắt đầu cảnh (đích thả "đầu cảnh"); đoạn sau là phần tiếp của cảnh ở hàng dưới. */
  sceneStart: boolean;
  clips: Clip[];
}

export interface PackedRow {
  key: string;
  segments: RowSegment[];
}

/**
 * Xếp clip vào hàng `cols` cột, cảnh ngắn nằm chung một hàng (mỗi đoạn có tiêu đề cảnh riêng) thay vì mỗi cảnh một
 * hàng — tập có cảnh 3 clip không còn bỏ trống nửa màn hình rộng. Cảnh dài hơn chỗ còn lại thì sang hàng kế (cảnh mới
 * không bắt đầu bằng mẩu một cột nếu còn ít hơn 2 cột trống, trừ khi cả cảnh chỉ có một clip).
 */
export function packRows(clips: Clip[], scenes: Map<number, { title: string; key: string }>, cols: number): PackedRow[] {
  const n = Math.max(1, Math.floor(cols));
  const rows: PackedRow[] = [];
  let cur: RowSegment[] = [];
  let used = 0;
  const flush = () => {
    if (cur.length) rows.push({ key: `r${cur[0].clips[0].id}`, segments: cur });
    cur = [];
    used = 0;
  };
  let i = 0;
  let group = 0;
  while (i < clips.length) {
    const sid = clips[i].scene_id;
    let j = i;
    while (j < clips.length && clips[j].scene_id === sid) j += 1;
    group += 1;
    const sc = sid != null ? scenes.get(sid) : undefined;
    const title = sc ? sc.title || `Cảnh ${sc.key}` : sid == null ? 'Chưa thuộc cảnh' : `Cảnh #${sid}`;
    const all = clips.slice(i, j);
    const dur = totalLength(all);
    if (used > 0 && n - used < Math.min(2, all.length)) flush();
    let k = 0;
    while (k < all.length) {
      if (used >= n) flush();
      const take = Math.min(n - used, all.length - k);
      cur.push({
        key: `g${group}:${k}`, sceneId: sid, title, count: all.length, durationMs: dur, firstIndex: i + k,
        sceneStart: k === 0, clips: all.slice(k, k + take),
      });
      used += take;
      k += take;
    }
    i = j;
  }
  flush();
  return rows;
}

export function cardGap(width: number): number {
  return width < 480 ? CARD_GAP_PHONE : CARD_GAP;
}

export function aspectRatio(aspect: string): number {
  return aspect === '16:9' ? 9 / 16 : aspect === '1:1' ? 1 : aspect === '4:5' ? 5 / 4 : aspect === '3:4' ? 4 / 3 : 16 / 9;
}

/** Chiều cao một hàng card (tính trước → ảo hoá không nhảy bố cục). */
export function cardRowHeight(width: number, cols: number, aspect: string, withLine: boolean): number {
  const gap = cardGap(width);
  const cardW = (width - gap * (cols - 1)) / cols;
  return Math.round(cardW * aspectRatio(aspect) + CARD_META_H + (withLine ? CARD_LINE_H : 0) + gap);
}
