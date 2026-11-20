/**
 * Toán timeline của Studio — bản sao **chính xác** của `backend/sagareel/render/layout.py` (`compute`,
 * `resolve_transition`, `_floor_frames`) + `render/profile.py::Profile.frame_ms`. Đối chiếu bằng
 * `lib/timeline.test.ts` với fixture JSON sinh từ backend thật (`lib/__fixtures__/gen_layout_fixture.py`).
 * Sửa `layout.py` → chạy lại script sinh fixture → test báo lệch.
 *
 * Luật Python cần giữ khi dịch: `round()` làm tròn về số chẵn (banker), `int()` cắt phần thập phân, `//` là floor,
 * và "truthiness" của Python (`0`, `""`, `None`, `{}`, `[]` đều là sai) khác JS (`{}`/`[]` là đúng).
 *
 * Kết quả dùng tên trường snake_case như backend để so sánh thẳng.
 */

// --- Python semantics -----------------------------------------------------------------------------------------------

/** `round(x)` của Python 3: nửa về số chẵn. */
export function pyRound(x: number): number {
  const f = Math.floor(x);
  const d = x - f;
  if (d > 0.5) return f + 1;
  if (d < 0.5) return f;
  return f % 2 === 0 ? f : f + 1;
}

/** `bool(v)` của Python. */
export function pyTruthy(v: unknown): boolean {
  if (v == null || v === false || v === 0 || v === '' || Number.isNaN(v)) return false;
  if (Array.isArray(v)) return v.length > 0;
  if (typeof v === 'object') return Object.keys(v as object).length > 0;
  return true;
}

/** `str(v)` của Python cho các giá trị JSON thường gặp. */
function pyStr(v: unknown): string {
  if (v === null || v === undefined) return 'None';
  if (v === true) return 'True';
  if (v === false) return 'False';
  return String(v);
}

const pyInt = (v: unknown): number => Math.trunc(Number(v));

function own<T>(table: Record<string, T>, key: string): T | undefined {
  return Object.prototype.hasOwnProperty.call(table, key) ? table[key] : undefined;
}

// --- Đầu vào / kết quả (khớp dataclass của layout.py) ---------------------------------------------------------------

export interface TransitionPreset {
  xfade?: string | null;
  duration_ms?: number | null;
  new_scene_duration_ms?: number | null;
  [k: string]: unknown;
}

export type TransitionTable = Record<string, TransitionPreset>;

export const DEFAULT_TRANSITIONS: TransitionTable = {
  cut: { xfade: null, duration_ms: 0 },
  fade: { xfade: 'fade', duration_ms: 500, new_scene_duration_ms: 700 },
  dissolve: { xfade: 'dissolve', duration_ms: 500, new_scene_duration_ms: 700 },
  fadeblack: { xfade: 'fadeblack', duration_ms: 1000, new_scene_duration_ms: 1000 },
  fadewhite: { xfade: 'fadewhite', duration_ms: 700, new_scene_duration_ms: 700 },
};

export interface ClipIn {
  id: number;
  dur_ms: number;
  key?: string;
  scene_id?: number | null;
  link?: string;
  transition?: Record<string, unknown> | null;
}

export interface LineIn {
  id: number;
  clip_id: number;
  offset_ms?: number;
  duration_ms?: number;
  text?: string;
  subtitle?: boolean;
}

export interface ItemIn {
  id: number;
  track: string;
  anchor_clip_id?: number | null;
  offset_ms?: number;
  duration_ms?: number | null;
  end_anchor?: Record<string, unknown> | null;
  media_duration_ms?: number | null;
  params?: Record<string, unknown>;
}

export interface SequenceIn {
  clips: ClipIn[];
  lines?: LineIn[];
  items?: ItemIn[];
}

export interface ClipSlot {
  id: number;
  index: number;
  start_ms: number;
  dur_ms: number;
  overlap_ms: number;
  transition: string;
  xfade: string | null;
  scene_id: number | null;
  key: string;
  end_ms: number;
}

export interface Placed {
  id: number;
  kind: 'dialogue' | 'music' | 'sfx' | 'title';
  start_ms: number;
  end_ms: number;
  clip_id: number | null;
  text: string;
  subtitle: boolean;
  params: Record<string, unknown>;
  dur_ms: number;
}

export interface LayoutWarning {
  code: string;
  message: string;
  [k: string]: unknown;
}

export interface Timeline {
  clips: ClipSlot[];
  dialogue: Placed[];
  sfx: Placed[];
  music: Placed[];
  titles: Placed[];
  total_ms: number;
  warnings: LayoutWarning[];
}

// --- Tính -----------------------------------------------------------------------------------------------------------

/** `_floor_frames`: làm tròn xuống số khung nguyên (ms); `fps` rỗng → giữ nguyên (int). */
export function floorFrames(ms: number, fps: number | null | undefined): number {
  if (!fps) return pyInt(ms);
  return pyRound((pyInt((ms * fps) / 1000 + 1e-9) * 1000) / fps);
}

/** `Profile.frames` / `Profile.frame_ms`: thời lượng clip ảnh (và khung đen) trước khi vào layout. */
export function frameMs(ms: number, fps = 30): number {
  const frames = Math.max(1, pyRound((ms * fps) / 1000));
  return pyRound((frames * 1000) / fps);
}

/** (tên preset, tên xfade, thời lượng ms chưa giới hạn) cho chỗ nối prev → cur. */
export function resolveTransition(
  prev: ClipIn,
  cur: ClipIn,
  transitions: TransitionTable,
  warnings: LayoutWarning[],
): [string, string | null, number] {
  const t = (pyTruthy(cur.transition) ? cur.transition : {}) as Record<string, unknown>;
  const rawName = pyTruthy(t.name) ? t.name : (cur.link ?? 'cut') === 'fade' ? 'fade' : 'cut';
  const name = pyStr(rawName);
  if (name === 'cut') return ['cut', null, 0];
  const preset = own(transitions, name);
  if (preset == null || !pyTruthy(preset.xfade)) {
    warnings.push({
      code: 'transition_unknown',
      clip_id: cur.id,
      name,
      message: `Chuyển cảnh “${name}” không có; dùng cắt thẳng.`,
    });
    return ['cut', null, 0];
  }
  let d: unknown = t.duration_ms;
  if (d == null) {
    const newScene = (prev.scene_id ?? null) !== (cur.scene_id ?? null);
    d = newScene ? preset.new_scene_duration_ms : null;
    if (d == null) d = 'duration_ms' in preset ? preset.duration_ms : 500;
  }
  return [name, pyStr(preset.xfade), Math.max(0, pyInt(d))];
}

/** Timeline tuyệt đối của một tập. `fps` (null = không làm tròn) làm tròn độ chồng về khung. */
export function compute(
  seq: SequenceIn,
  opts: { transitions?: TransitionTable | null; fps?: number | null } = {},
): Timeline {
  const table = opts.transitions ?? DEFAULT_TRANSITIONS;
  const fps = opts.fps === undefined ? 30 : opts.fps;
  const warnings: LayoutWarning[] = [];

  const slots: ClipSlot[] = [];
  let cursor = 0;
  let prevIn: ClipIn | null = null;
  seq.clips.forEach((c, i) => {
    const dur = Math.max(0, pyInt(c.dur_ms));
    let name = 'cut';
    let xf: string | null = null;
    let overlap = 0;
    if (prevIn !== null) {
      let want: number;
      [name, xf, want] = resolveTransition(prevIn, c, table, warnings);
      if (want) {
        const limit = Math.floor(Math.min(slots[slots.length - 1].dur_ms, dur) / 2);
        overlap = floorFrames(Math.min(want, limit), fps);
        if (want > limit) {
          warnings.push({
            code: 'transition_clamped',
            clip_id: c.id,
            wanted_ms: want,
            used_ms: overlap,
            message: 'Chuyển cảnh dài hơn nửa clip ngắn hơn nên đã được rút ngắn.',
          });
        }
        if (overlap <= 0) {
          name = 'cut';
          xf = null;
          overlap = 0;
        }
      }
    }
    const start = cursor - overlap;
    slots.push({
      id: c.id,
      index: i,
      start_ms: start,
      dur_ms: dur,
      overlap_ms: overlap,
      transition: name,
      xfade: xf,
      scene_id: c.scene_id ?? null,
      key: c.key ?? '',
      end_ms: start + dur,
    });
    cursor = start + dur;
    prevIn = c;
  });
  const total = cursor;
  const byId = new Map<number, ClipSlot>();
  for (const s of slots) byId.set(s.id, s);

  const placed = (
    p: Omit<Placed, 'dur_ms' | 'subtitle' | 'params' | 'text' | 'clip_id'> & Partial<Placed>,
  ): Placed => ({
    clip_id: null,
    text: '',
    subtitle: false,
    params: {},
    ...p,
    dur_ms: p.end_ms - p.start_ms,
  });

  const dialogue: Placed[] = [];
  for (const ln of seq.lines ?? []) {
    const slot = byId.get(ln.clip_id);
    if (slot === undefined) {
      warnings.push({ code: 'dialogue_orphan', line_id: ln.id, message: 'Câu thoại thuộc clip không có trong tập; bỏ qua.' });
      continue;
    }
    const start = slot.start_ms + pyInt(ln.offset_ms ?? 0);
    const end = start + Math.max(0, pyInt(ln.duration_ms ?? 0));
    if (end > total) {
      warnings.push({ code: 'dialogue_overflow', line_id: ln.id, message: 'Câu thoại kéo quá cuối tập; phần thừa bị cắt.' });
    }
    dialogue.push(
      placed({
        id: ln.id,
        kind: 'dialogue',
        start_ms: start,
        end_ms: total ? Math.min(end, total) : end,
        clip_id: ln.clip_id,
        text: ln.text ?? '',
        subtitle: ln.subtitle ?? true,
      }),
    );
  }
  const byStart = (a: Placed, b: Placed) => a.start_ms - b.start_ms || a.id - b.id;
  dialogue.sort(byStart);

  const tracks: Record<'music' | 'sfx' | 'title', Placed[]> = { music: [], sfx: [], title: [] };
  for (const it of seq.items ?? []) {
    if (!Object.prototype.hasOwnProperty.call(tracks, it.track)) {
      warnings.push({ code: 'track_unknown', item_id: it.id, message: `Track “${it.track}” không hợp lệ.` });
      continue;
    }
    const track = it.track as keyof typeof tracks;
    const params = it.params ?? {};
    let base = 0;
    const anchorId = it.anchor_clip_id ?? null;
    const anchor = anchorId !== null ? (byId.get(anchorId) ?? null) : null;
    if (anchorId !== null && anchor === null) {
      warnings.push({
        code: 'anchor_missing',
        item_id: it.id,
        message: 'Clip neo không còn trong tập; item được đặt từ đầu tập.',
      });
    }
    if (anchor !== null) base = anchor.start_ms;
    const start = Math.max(0, base + pyInt(it.offset_ms ?? 0));
    let end: number | null = null;
    if (it.duration_ms != null) {
      end = start + Math.max(0, pyInt(it.duration_ms));
    } else if (pyTruthy(it.end_anchor)) {
      const ea = it.end_anchor as Record<string, unknown>;
      if (pyTruthy(ea.sequence_end)) {
        end = total;
      } else if (ea.clip_id != null && byId.has(Number(ea.clip_id))) {
        end = byId.get(Number(ea.clip_id))!.end_ms;
      }
    }
    if (end === null) {
      if (track === 'music' && (pyTruthy(params.loop) || !pyTruthy(it.media_duration_ms))) end = total;
      else if (pyTruthy(it.media_duration_ms)) end = start + pyInt(it.media_duration_ms);
      else if (anchor !== null) end = anchor.end_ms;
      else end = total;
    }
    end = Math.min(end, total);
    if (end <= start) {
      warnings.push({ code: 'item_empty', item_id: it.id, message: 'Item nằm ngoài thời lượng tập; bỏ qua.' });
      continue;
    }
    tracks[track].push(
      placed({
        id: it.id,
        kind: track,
        start_ms: start,
        end_ms: end,
        clip_id: anchorId,
        text: pyStr('text' in params ? params.text : ''),
        params: { ...params },
      }),
    );
  }
  for (const lst of Object.values(tracks)) lst.sort(byStart);

  return {
    clips: slots,
    dialogue,
    sfx: tracks.sfx,
    music: tracks.music,
    titles: tracks.title,
    total_ms: total,
    warnings,
  };
}

// --- Dựng đầu vào từ dữ liệu op (gần đúng `render/pipeline.py::load`) --------------------------------------------

/** Mục `clip.list` mà timeline cần. */
export interface ClipRow {
  id: number;
  key: string;
  title?: string;
  scene_id: number | null;
  duration_ms: number;
  link: string;
  transition: Record<string, unknown> | null;
  engine?: string;
  trim_in_ms?: number;
  trim_out_ms?: number;
  speed?: number;
  image_take_id?: number | null;
  video_take_id?: number | null;
  input_media_id?: number | null;
  shot_size?: string;
  motion_prompt?: string;
  image_prompt?: string;
  grok_audio?: string;
  motion_preset?: string;
  subtitle_mode?: string;
  flags?: Record<string, unknown>;
  review?: string;
  updated_at?: string;
  /** Media của take đang chọn (`clip.list`, `services/clips.py::selected_media`). */
  image_media_id?: number | null;
  video_media_id?: number | null;
  /** Ảnh, không thì video — thumbnail ở `/media/<id>/thumb`; null nếu file đã vào thùng rác. */
  thumb_media_id?: number | null;
  /** Độ dài video provider đang chọn (ms). */
  source_duration_ms?: number | null;
}

export interface DialogueRow {
  id: number;
  clip_id: number;
  text: string;
  offset_ms: number;
  subtitle: boolean;
  source: string;
  speaker_asset_id: number | null;
  translations?: Record<string, string>;
  audio_take_id?: number | null;
  /** Media + độ dài âm thanh của take đang chọn (`dialogue.list`); null = chưa đọc → dùng ước lượng. */
  audio_media_id?: number | null;
  audio_duration_ms?: number | null;
  emotion?: string;
  voice_override?: Record<string, unknown>;
  updated_at?: string;
}

export interface TimelineItemRow {
  id: number;
  sequence_id: number;
  track: string;
  anchor_clip_id: number | null;
  offset_ms: number;
  end_anchor: Record<string, unknown> | null;
  duration_ms: number | null;
  media_id: number | null;
  sound_id: number | null;
  params: Record<string, unknown>;
  updated_at?: string;
}

/** Ước lượng thời lượng câu thoại khi chưa có âm thanh — đúng công thức của backend. */
export function estimateLineMs(text: string): number {
  const words = text.trim().split(/\s+/).filter(Boolean).length;
  return Math.max(1000, 300 * words);
}

/**
 * `SequenceIn` từ `clip.list` + `dialogue.list` + `timeline.list`. Gần đúng phía dựng: clip video thật dùng độ dài
 * nguồn sau trim/tốc độ (UI chưa có trường này, nên dùng `duration_ms` làm tròn khung như clip ảnh); thoại chưa có
 * âm thanh dùng ước lượng theo số từ; nhạc/SFX không có media bị bỏ như backend.
 */
export function buildInput(
  clips: ClipRow[],
  lines: DialogueRow[],
  items: TimelineItemRow[],
  opts: {
    fps?: number;
    lang?: string;
    primaryLang?: string;
    mediaDuration?: (id: number) => number | null;
    /** Độ dài nguồn (ms) của video take đang chọn theo clip — có thì clip video dài theo nguồn như khi dựng. */
    videoSourceMs?: (clipId: number) => number | null;
  } = {},
): SequenceIn {
  const fps = opts.fps ?? 30;
  const primary = !opts.lang || opts.lang === opts.primaryLang;
  const clipIds = new Set(clips.map((c) => c.id));
  const clipById = new Map(clips.map((c) => [c.id, c]));
  return {
    clips: clips.map((c) => ({
      id: c.id,
      dur_ms: clipDurMs(c, opts.videoSourceMs?.(c.id) ?? null, fps),
      key: c.key,
      scene_id: c.scene_id,
      link: c.link || 'cut',
      transition: { ...(c.transition ?? {}) },
    })),
    lines: lines
      .filter((l) => clipIds.has(l.clip_id))
      .map((l) => {
        const text = primary ? l.text : String(l.translations?.[opts.lang!] || l.text);
        const dur = l.audio_duration_ms && l.audio_duration_ms > 0 ? l.audio_duration_ms : estimateLineMs(text);
        const mode = clipById.get(l.clip_id)?.subtitle_mode || 'auto';
        return {
          id: l.id,
          clip_id: l.clip_id,
          offset_ms: l.offset_ms || 0,
          duration_ms: dur,
          text,
          subtitle: Boolean(l.subtitle) && mode !== 'off',
        };
      }),
    items: items
      .filter((it) => !((it.track === 'music' || it.track === 'sfx') && it.media_id == null && it.sound_id == null))
      .map((it) => ({
        id: it.id,
        track: it.track,
        anchor_clip_id: it.anchor_clip_id,
        offset_ms: it.offset_ms || 0,
        duration_ms: it.duration_ms,
        end_anchor: it.end_anchor,
        media_duration_ms: it.media_id != null ? (opts.mediaDuration?.(it.media_id) ?? null) : null,
        params: { ...(it.params ?? {}) },
      })),
  };
}

/**
 * Độ dài clip khi dựng (`pipeline.load`): clip có video take → `normalize.plan` = độ dài nguồn sau trim và tốc độ
 * (`clip.duration_ms` bị bỏ qua), làm tròn khung; trim cắt hết → khung đen theo `duration_ms`. Còn lại (ảnh, khung
 * đen) = `frame_ms(duration_ms)`.
 */
export function clipDurMs(c: Pick<ClipRow, 'duration_ms' | 'video_take_id' | 'trim_in_ms' | 'trim_out_ms' | 'speed'>, srcMs: number | null, fps = 30): number {
  if (c.video_take_id != null && srcMs != null && srcMs > 0) {
    const avail = Math.max(0, pyRound((srcMs - (c.trim_in_ms ?? 0) - (c.trim_out_ms ?? 0)) / Math.max(c.speed ?? 1, 0.01)));
    if (avail > 0) return frameMs(avail, fps);
  }
  return frameMs(c.duration_ms, fps);
}

/**
 * Trim mép phải về độ dài `wantMs` (ms đầu ra). Clip video: đổi `trim_out_ms` (giới hạn trong nguồn); clip ảnh: đổi
 * `duration_ms`. Trả giá trị cần ghi + độ dài thực sau giới hạn.
 */
export function trimValues(
  c: Pick<ClipRow, 'duration_ms' | 'video_take_id' | 'trim_in_ms' | 'trim_out_ms' | 'speed'>,
  wantMs: number,
  srcMs: number | null,
  fps = 30,
): { values: { duration_ms?: number; trim_out_ms?: number }; ms: number; limited: boolean } {
  if (c.video_take_id != null && srcMs != null && srcMs > 0) {
    const speed = Math.max(c.speed ?? 1, 0.01);
    const tin = c.trim_in_ms ?? 0;
    const maxOut = Math.max(0, srcMs - tin); // dùng hết nguồn
    const wantSrc = Math.round(wantMs * speed);
    const used = Math.min(maxOut, Math.max(Math.round(MIN_CLIP_MS * speed), wantSrc));
    const trimOut = Math.max(0, srcMs - tin - used);
    return { values: { trim_out_ms: trimOut }, ms: clipDurMs({ ...c, trim_out_ms: trimOut }, srcMs, fps), limited: used !== wantSrc };
  }
  const r = trimDuration(0, wantMs, { fps });
  return { values: { duration_ms: r.ms }, ms: r.ms, limited: r.limited };
}

/** `preset.list kind=transition` → bảng transition (`data` của từng preset theo `key`). */
export function transitionTable(rows: { key: string; data?: Record<string, unknown> }[] | undefined): TransitionTable {
  if (!rows || rows.length === 0) return DEFAULT_TRANSITIONS;
  const out: TransitionTable = {};
  for (const r of rows) out[r.key] = { ...(r.data ?? {}) } as TransitionPreset;
  return out;
}

// --- Hiển thị: timecode, thước, toạ độ ----------------------------------------------------------------------------

const pad = (n: number, w = 2) => String(n).padStart(w, '0');

/** `HH:MM:SS:FF` (DESIGN §9). */
export function timecode(ms: number, fps = 30): string {
  const t = Math.max(0, ms);
  const totalFrames = Math.floor((t * fps) / 1000 + 1e-6);
  const ff = totalFrames % fps;
  const secs = Math.floor(totalFrames / fps);
  return `${pad(Math.floor(secs / 3600))}:${pad(Math.floor(secs / 60) % 60)}:${pad(secs % 60)}:${pad(ff)}`;
}

/** `01:12` (dưới 1 giờ) hoặc `1:01:12`. */
export function shortTime(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(s / 3600);
  const body = `${pad(Math.floor(s / 60) % 60)}:${pad(s % 60)}`;
  return h ? `${h}:${body}` : body;
}

/** "4,0 s" · "1 ph 12 s" (DESIGN §9). */
export function clipSeconds(ms: number): string {
  if (ms < 60_000) return `${(ms / 1000).toFixed(1).replace('.', ',')} s`;
  const s = Math.round(ms / 1000);
  return `${Math.floor(s / 60)} ph ${s % 60} s`;
}

/** Vạch thước: bước chính (có nhãn) và phụ theo mật độ px/giây. */
export function rulerStep(pxPerSecond: number): { major: number; minor: number } {
  const steps = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600];
  const major = steps.find((s) => s * pxPerSecond >= 72) ?? 600;
  const minor = major >= 10 ? major / 5 : major === 5 ? 1 : major / 2;
  return { major: major * 1000, minor: minor * 1000 };
}

export function rulerTicks(totalMs: number, pxPerSecond: number): { ms: number; major: boolean }[] {
  const { major, minor } = rulerStep(pxPerSecond);
  const out: { ms: number; major: boolean }[] = [];
  const end = Math.max(totalMs, major);
  for (let t = 0; t <= end + 1; t += minor) out.push({ ms: t, major: t % major === 0 });
  return out;
}

export const ZOOM_MIN = 8; // px/giây
export const ZOOM_MAX = 400;

export function clampZoom(pxPerSecond: number): number {
  return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, pxPerSecond));
}

/** Zoom vừa khung: cả tập nằm trong `widthPx`. */
export function fitZoom(totalMs: number, widthPx: number): number {
  if (totalMs <= 0 || widthPx <= 0) return 40;
  return clampZoom((widthPx / totalMs) * 1000);
}

// --- Sửa: kéo đổi thứ tự, trim, nam châm -------------------------------------------------------------------------

/** Vị trí thả (0…n) khi kéo một clip: trước clip đầu tiên có điểm giữa nằm sau con trỏ. */
export function dropIndex(slots: Pick<ClipSlot, 'start_ms' | 'end_ms'>[], pointerMs: number): number {
  for (let i = 0; i < slots.length; i += 1) {
    const mid = (slots[i].start_ms + slots[i].end_ms) / 2;
    if (pointerMs < mid) return i;
  }
  return slots.length;
}

/**
 * Tham số `clip.move` khi đưa clip `id` tới vị trí thả `to` (chỉ số trong thứ tự hiện tại, 0…n):
 * `after_clip_id` = 0 → đầu tập; null = không đổi chỗ.
 */
export function moveTarget(order: number[], id: number, to: number): { after_clip_id: number } | null {
  const from = order.indexOf(id);
  if (from < 0 || to === from || to === from + 1) return null;
  const rest = order.filter((x) => x !== id);
  const at = to > from ? to - 1 : to;
  return { after_clip_id: at === 0 ? 0 : rest[at - 1] };
}

/** Thứ tự mới sau khi chuyển `id` tới vị trí thả `to` (để cập nhật lạc quan). */
export function reorder(order: number[], id: number, to: number): number[] {
  const from = order.indexOf(id);
  if (from < 0) return order;
  const rest = order.filter((x) => x !== id);
  const at = to > from ? to - 1 : to;
  rest.splice(Math.max(0, Math.min(at, rest.length)), 0, id);
  return rest;
}

export const MIN_CLIP_MS = 500;

/** Thời lượng mới khi kéo mép phải clip `deltaMs` (làm tròn khung, giới hạn [min, max]). */
export function trimDuration(durMs: number, deltaMs: number, opts: { fps?: number; max?: number | null } = {}): {
  ms: number;
  limited: boolean;
} {
  const want = durMs + deltaMs;
  const max = opts.max ?? 600_000;
  const clamped = Math.min(max, Math.max(MIN_CLIP_MS, want));
  return { ms: frameMs(clamped, opts.fps ?? 30), limited: clamped !== want };
}

/** Nam châm: điểm gần nhất trong `targets` cách `ms` ≤ `thresholdMs`, không có thì giữ nguyên. */
export function snap(ms: number, targets: number[], thresholdMs: number): number {
  let best = ms;
  let dist = thresholdMs + 1;
  for (const t of targets) {
    const d = Math.abs(t - ms);
    if (d <= thresholdMs && d < dist) {
      best = t;
      dist = d;
    }
  }
  return best;
}

/** Clip đang có mặt tại thời điểm `ms` (clip sau thắng trong vùng chồng). */
export function clipAt(slots: ClipSlot[], ms: number): ClipSlot | null {
  let hit: ClipSlot | null = null;
  for (const s of slots) if (ms >= s.start_ms && ms < s.end_ms) hit = s;
  return hit ?? (slots.length && ms >= slots[slots.length - 1].end_ms ? slots[slots.length - 1] : null);
}

/** Clip neo cho một mục đặt tại `ms`: clip chứa thời điểm đó + offset từ đầu clip. */
export function anchorFor(slots: ClipSlot[], ms: number): { anchor_clip_id: number | null; offset_ms: number } {
  const c = clipAt(slots, ms);
  if (!c) return { anchor_clip_id: null, offset_ms: Math.max(0, Math.round(ms)) };
  return { anchor_clip_id: c.id, offset_ms: Math.max(0, Math.round(ms - c.start_ms)) };
}
