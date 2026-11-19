/**
 * Dây chuyền của tập → 8 chặng hiển thị cố định (ux-architecture §2.2). Hàm thuần: nguồn dữ liệu (`sequence.get` +
 * `clip.list` hôm nay, `sequence.status` của Task J sau này) đổi được mà UI không đổi.
 *
 * Hình dạng đầu vào được đọc khoan dung vì Task J chưa chốt:
 * - workflow preset: `{ steps: (string | { key, enabled?, bucket? })[] }` hoặc mảng trực tiếp; gate là bước có key
 *   `gate:<tên>`; bước tắt = `enabled: false` (hoặc gate có `enabled: false`).
 * - `sequence.pipeline`: `{ steps: { <key>: StepState } }` hoặc `{ <key>: StepState }`, với StepState là chuỗi trạng
 *   thái hoặc `{ status, progress?, done?, total?, failed?, stale? }`.
 * - `sequence.gates`: `{ 'gate:<tên>' | '<tên>': 'waiting' | 'approved' | … | { status, enabled? } }`.
 */

export type StageKey = 'script' | 'breakdown' | 'frames' | 'animatic' | 'video' | 'audio' | 'edit' | 'export';

export type StageStatus = 'todo' | 'running' | 'wait' | 'done' | 'failed' | 'stale' | 'skipped';

export type GateStatus = 'waiting' | 'approved' | 'rejected' | 'pending';

export interface StageGate {
  key: string;
  status: GateStatus;
}

export interface Stage {
  key: StageKey;
  /** 1…8 */
  index: number;
  label: string;
  status: StageStatus;
  /** 0–100 khi đang chạy. */
  progress: number | null;
  /** Số đi kèm trạng thái (số lỗi, số clip cũ, số clip…); null = không ghi. */
  count: number | null;
  /** Nhãn ngắn của số: "3 cũ", "2 lỗi", "48 clip", "62%". */
  countLabel: string | null;
  /** Gate ngay sau chặng (gate cuối nếu có nhiều); null = workflow không có gate ở đó. */
  gate: StageGate | null;
  /** Các bước workflow gom vào chặng. */
  steps: string[];
}

export const STAGE_DEFS: { key: StageKey; label: string; steps: string[]; gates: string[] }[] = [
  { key: 'script', label: 'Kịch bản', steps: ['outline', 'write_script'], gates: ['gate:outline'] },
  {
    key: 'breakdown',
    label: 'Phân cảnh',
    steps: ['split_clips', 'image_prompts', 'motion_prompts', 'dialogue'],
    gates: ['gate:audio_plan'],
  },
  { key: 'frames', label: 'Khung hình', steps: ['generate_images', 'auto_select'], gates: ['gate:storyboard'] },
  { key: 'animatic', label: 'Animatic', steps: ['render_animatic'], gates: ['gate:animatic'] },
  { key: 'video', label: 'Video', steps: ['generate_videos'], gates: ['gate:pilot'] },
  { key: 'audio', label: 'Âm thanh', steps: ['generate_audio', 'subtitles'], gates: [] },
  { key: 'edit', label: 'Dựng', steps: ['render_preview', 'render'], gates: ['gate:preview', 'gate:final'] },
  { key: 'export', label: 'Xuất', steps: ['export'], gates: [] },
];

/** Workflow mặc định khi chưa có preset (spec §13 + addendum: animatic trước video, `gate:pilot` tuỳ chọn = tắt). */
export const DEFAULT_WORKFLOW: string[] = [
  'outline', 'write_script', 'gate:outline',
  'split_clips', 'image_prompts', 'motion_prompts', 'dialogue', 'gate:audio_plan',
  'generate_images', 'auto_select', 'gate:storyboard',
  'render_animatic', 'gate:animatic',
  'generate_videos',
  'generate_audio', 'subtitles',
  'render_preview', 'gate:preview', 'render', 'gate:final',
  'export',
];

/** Bước cấp dự án — không nằm trên dây chuyền tập. */
const PROJECT_LEVEL = new Set(['write_bible']);

const STAGE_OF_STEP = new Map<string, StageKey>();
for (const d of STAGE_DEFS) {
  for (const s of d.steps) STAGE_OF_STEP.set(s, d.key);
  for (const g of d.gates) STAGE_OF_STEP.set(g, d.key);
}

const STAGE_KEYS = new Set<string>(STAGE_DEFS.map((d) => d.key));

/** Đếm cờ clip (fallback khi chưa có `sequence.status`). */
export interface ClipCounts {
  clips: number;
  /** Tập đã có kịch bản (sequence.script khác rỗng). */
  hasScript?: boolean;
  withImage?: number;
  withVideo?: number;
  staleImage?: number;
  staleVideo?: number;
  drift?: number;
  failedImage?: number;
  failedVideo?: number;
}

interface NormStep {
  key: string;
  enabled: boolean;
  bucket: StageKey | null;
}

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v);
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);

function normalizeWorkflow(preset: unknown): NormStep[] {
  const raw = Array.isArray(preset) ? preset : isObj(preset) && Array.isArray(preset.steps) ? preset.steps : null;
  const list: unknown[] = raw && raw.length > 0 ? raw : DEFAULT_WORKFLOW;
  const out: NormStep[] = [];
  for (const item of list) {
    if (typeof item === 'string') {
      // spec viết `auto_select|gate:storyboard` = một trong hai: tách thành hai bước.
      for (const k of item.split('|')) if (k.trim()) out.push({ key: k.trim(), enabled: true, bucket: null });
    } else if (isObj(item) && typeof item.key === 'string') {
      const bucket = typeof item.bucket === 'string' && STAGE_KEYS.has(item.bucket) ? (item.bucket as StageKey) : null;
      out.push({ key: item.key, enabled: item.enabled !== false, bucket });
    }
  }
  return out.filter((s) => !PROJECT_LEVEL.has(s.key));
}

function stepState(pipeline: unknown, key: string): Obj | null {
  if (!isObj(pipeline)) return null;
  const src = isObj(pipeline.steps) ? pipeline.steps : pipeline;
  const v = src[key];
  if (typeof v === 'string') return { status: v };
  return isObj(v) ? v : null;
}

const RUNNING = new Set(['running', 'queued', 'in_progress', 'started']);
const DONE = new Set(['done', 'succeeded', 'success', 'complete', 'completed', 'ok']);
const FAILED = new Set(['failed', 'error', 'needs_attention', 'interrupted']);
const WAITING = new Set(['waiting', 'waiting_review', 'open', 'pending_review', 'awaiting']);
const SKIPPED = new Set(['skipped', 'disabled', 'off']);

function gateState(gates: unknown, pipeline: unknown, key: string): { status: GateStatus; enabled: boolean } | null {
  const short = key.replace(/^gate:/, '');
  let v: unknown = isObj(gates) ? (gates[key] ?? gates[short]) : undefined;
  if (v === undefined) v = stepState(pipeline, key) ?? undefined;
  if (v === undefined) return null;
  const o: Obj = typeof v === 'string' ? { status: v } : typeof v === 'boolean' ? { enabled: v } : isObj(v) ? v : {};
  const enabled = o.enabled !== false && !SKIPPED.has(String(o.status ?? ''));
  const s = String(o.status ?? '');
  const status: GateStatus = WAITING.has(s)
    ? 'waiting'
    : s === 'approved' || DONE.has(s)
      ? 'approved'
      : s === 'rejected'
        ? 'rejected'
        : 'pending';
  return { status, enabled };
}

/** Trạng thái chặng khi không có dữ liệu pipeline: đọc từ cờ clip. */
function fallbackStage(key: StageKey, c: ClipCounts | null): Pick<Stage, 'status' | 'count' | 'countLabel' | 'progress'> {
  const none = { status: 'todo' as StageStatus, count: null, countLabel: null, progress: null };
  if (!c) return none;
  const clips = c.clips;
  switch (key) {
    case 'script':
      return c.hasScript || clips > 0 ? { ...none, status: 'done' } : none;
    case 'breakdown':
      return clips > 0 ? { ...none, status: 'done', count: clips, countLabel: `${clips} clip` } : none;
    case 'frames':
    case 'video': {
      const have = (key === 'frames' ? c.withImage : c.withVideo) ?? 0;
      const failed = (key === 'frames' ? c.failedImage : c.failedVideo) ?? 0;
      const stale = (key === 'frames' ? c.staleImage : c.staleVideo) ?? 0;
      if (clips === 0) return none;
      if (failed > 0) return { ...none, status: 'failed', count: failed, countLabel: `${failed} lỗi` };
      if (stale > 0) return { ...none, status: 'stale', count: stale, countLabel: `${stale} cũ` };
      if (have >= clips) return { ...none, status: 'done' };
      if (have > 0) return { ...none, status: 'running', count: have, countLabel: `${have}/${clips}`, progress: Math.round((have / clips) * 100) };
      return none;
    }
    default:
      return none;
  }
}

/**
 * Gom workflow + trạng thái thành đúng 8 chặng. Bước lạ rơi vào `bucket` của nó; không có bucket → chặng của bước đứng
 * trước (bước lạ đầu tiên không có gì đứng trước → Kịch bản).
 */
export function toStages(
  workflowPreset: unknown,
  pipeline: unknown,
  gates: unknown,
  counts: ClipCounts | null,
): Stage[] {
  const steps = normalizeWorkflow(workflowPreset);
  const byStage = new Map<StageKey, NormStep[]>(STAGE_DEFS.map((d) => [d.key, []]));
  let prev: StageKey = 'script';
  for (const s of steps) {
    const stage = s.bucket ?? STAGE_OF_STEP.get(s.key) ?? prev;
    byStage.get(stage)!.push(s);
    prev = stage;
  }
  const hasPipeline = isObj(pipeline) && Object.keys(isObj(pipeline.steps) ? pipeline.steps : pipeline).length > 0;

  return STAGE_DEFS.map((def, i) => {
    const mine = byStage.get(def.key)!;
    const work = mine.filter((s) => !s.key.startsWith('gate:'));
    const gateSteps = mine.filter((s) => s.key.startsWith('gate:'));

    // Gate: gate cuối cùng còn bật của chặng; gate đang chờ thắng.
    let gate: StageGate | null = null;
    for (const g of gateSteps) {
      const st = gateState(gates, pipeline, g.key);
      const enabled = g.enabled && (st ? st.enabled : true);
      if (!enabled) continue;
      const cand: StageGate = { key: g.key, status: st?.status ?? 'pending' };
      if (!gate || gate.status !== 'waiting') gate = cand;
    }

    const base: Stage = {
      key: def.key, index: i + 1, label: def.label, status: 'todo', progress: null, count: null, countLabel: null,
      gate, steps: mine.map((s) => s.key),
    };

    const enabledWork = work.filter((s) => s.enabled);
    if (work.length > 0 && enabledWork.length === 0 && !gate) return { ...base, status: 'skipped' };
    if (mine.length === 0) return { ...base, status: 'skipped' };

    let status: StageStatus = 'todo';
    let progress: number | null = null;
    let count: number | null = null;
    let countLabel: string | null = null;

    if (hasPipeline) {
      const states = enabledWork.map((s) => stepState(pipeline, s.key));
      const st = states.map((o) => String(o?.status ?? ''));
      const failedN = states.reduce((n, o) => n + (num(o?.failed) ?? 0), 0);
      const staleN = states.reduce((n, o) => n + (num(o?.stale) ?? 0), 0);
      if (st.some((s) => FAILED.has(s)) || failedN > 0) {
        status = 'failed';
        count = failedN || st.filter((s) => FAILED.has(s)).length;
        countLabel = `${count} lỗi`;
      } else if (st.some((s) => RUNNING.has(s))) {
        status = 'running';
        const o = states.find((x) => RUNNING.has(String(x?.status ?? '')));
        const p = num(o?.progress) ?? (num(o?.total) ? ((num(o?.done) ?? 0) / num(o?.total)!) * 100 : null);
        progress = p == null ? null : Math.round(p);
        countLabel = progress == null ? null : `${progress}%`;
      } else if (staleN > 0) {
        status = 'stale';
        count = staleN;
        countLabel = `${staleN} cũ`;
      } else if (st.length > 0 && st.every((s) => DONE.has(s) || SKIPPED.has(s))) {
        status = 'done';
      } else if (st.some((s) => DONE.has(s))) {
        status = 'running';
      }
      // Cờ clip vẫn bổ sung "đã cũ"/"lỗi" cho chặng đã xong.
      if (status === 'done' && counts) {
        const fb = fallbackStage(def.key, counts);
        if (fb.status === 'stale' || fb.status === 'failed') ({ status, count, countLabel } = { ...fb });
        else if (def.key === 'breakdown' && counts.clips > 0) ({ count, countLabel } = { count: counts.clips, countLabel: `${counts.clips} clip` });
      }
    } else {
      ({ status, count, countLabel, progress } = fallbackStage(def.key, counts));
    }

    if (gate?.status === 'waiting') status = 'wait';
    return { ...base, status, progress, count, countLabel };
  });
}

/** Chặng hiện tại để làm nổi: chặng chờ bạn đầu tiên, nếu không thì chặng đầu chưa xong. */
export function currentStage(stages: Stage[]): Stage | null {
  return (
    stages.find((s) => s.status === 'wait') ??
    stages.find((s) => s.status !== 'done' && s.status !== 'skipped') ??
    stages.at(-1) ??
    null
  );
}

/** Đường dẫn công cụ của một chặng (đã lọc theo tập). `base` = `/p/:pid`. */
export function stageHref(base: string, seqId: string | number, key: StageKey): string {
  switch (key) {
    case 'script':
      return `${base}/s/${seqId}/script`;
    case 'breakdown':
      return `${base}/s/${seqId}`;
    case 'frames':
      return `${base}/review?seq=${seqId}&part=image`;
    case 'animatic':
    case 'edit':
      return `${base}/studio/${seqId}`;
    case 'video':
      return `${base}/review?seq=${seqId}&part=video`;
    case 'audio':
      return `${base}/s/${seqId}/audio`;
    case 'export':
      return `${base}/export?seq=${seqId}`;
  }
}

/** Chặng ứng với màn đang mở (để tô "đang mở"). `rest` = phần path sau `/p/:pid/`. */
export function stageForPath(rest: string, search: string): StageKey | null {
  const q = new URLSearchParams(search);
  const parts = rest.replace(/^\/+|\/+$/g, '').split('/');
  if (parts[0] === 's' && parts[1]) {
    if (parts[2] === 'script') return 'script';
    if (parts[2] === 'audio') return 'audio';
    return 'breakdown';
  }
  if (parts[0] === 'studio') return 'edit';
  if (parts[0] === 'review') return q.get('part') === 'video' ? 'video' : 'frames';
  if (parts[0] === 'export') return 'export';
  return null;
}

/**
 * Tập đang xem theo route: `s/:seqId/*`, `studio/:seqId`, hoặc `review|export` có đúng MỘT `?seq=` (lô `seq=a,b` thì
 * không có thanh chặng). Trả null nếu route không thuộc một tập.
 */
export function seqFromRoute(rest: string, search: string): string | null {
  const parts = rest.replace(/^\/+|\/+$/g, '').split('/');
  if ((parts[0] === 's' || parts[0] === 'studio') && parts[1] && /^\d+$/.test(parts[1])) return parts[1];
  if (parts[0] === 'review' || parts[0] === 'export') {
    const seq = new URLSearchParams(search).get('seq');
    if (seq && /^\d+$/.test(seq)) return seq;
  }
  return null;
}

/** Đếm cờ clip từ `clip.list` (fallback cho `sequence.status`). */
export function countClipFlags(
  items: { image_take_id?: number | null; video_take_id?: number | null; flags?: Record<string, unknown> | null }[],
  hasScript: boolean,
): ClipCounts {
  const c: ClipCounts = {
    clips: items.length, hasScript, withImage: 0, withVideo: 0, staleImage: 0, staleVideo: 0, drift: 0,
    failedImage: 0, failedVideo: 0,
  };
  for (const it of items) {
    const f = it.flags ?? {};
    if (it.image_take_id != null) c.withImage! += 1;
    if (it.video_take_id != null) c.withVideo! += 1;
    if (f.stale_image) c.staleImage! += 1;
    if (f.stale_video) c.staleVideo! += 1;
    if (f.drift) c.drift! += 1;
    if (f.failed_image) c.failedImage! += 1;
    if (f.failed_video) c.failedVideo! += 1;
  }
  return c;
}

/** Số gate đang chờ trong `sequence.gates` (+ `sequence.pipeline`) của một tập. */
export function openGates(gates: unknown, pipeline: unknown): number {
  const keys = new Set<string>();
  if (isObj(gates)) for (const k of Object.keys(gates)) keys.add(k.startsWith('gate:') ? k : `gate:${k}`);
  if (isObj(pipeline)) {
    const src = isObj(pipeline.steps) ? pipeline.steps : pipeline;
    for (const k of Object.keys(src)) if (k.startsWith('gate:')) keys.add(k);
  }
  let n = 0;
  for (const k of keys) {
    const st = gateState(gates, pipeline, k);
    if (st?.enabled && st.status === 'waiting') n += 1;
  }
  return n;
}

const STATUS_TEXT: Record<StageStatus, string> = {
  todo: 'chưa tới',
  running: 'đang chạy',
  wait: 'chờ bạn',
  done: 'xong',
  failed: 'có lỗi',
  stale: 'có clip đã cũ',
  skipped: 'bỏ qua',
};

/** Mô tả đầy đủ cho tooltip / sr-only: "Chặng 3: Khung hình — có clip đã cũ (Alt 3)". */
export function stageDescription(s: Stage): string {
  const extra = s.countLabel ? ` · ${s.countLabel}` : '';
  const gate = s.gate?.status === 'waiting' ? ' · gate chờ bạn duyệt' : '';
  return `Chặng ${s.index}: ${s.label} — ${STATUS_TEXT[s.status]}${extra}${gate} (Alt ${s.index})`;
}
