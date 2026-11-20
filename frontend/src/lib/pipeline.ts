/**
 * Dây chuyền của tập → 8 chặng hiển thị cố định (ux-architecture §2.2). Hàm thuần.
 *
 * Nguồn sự thật (backend `services/pipeline`):
 * - workflow preset (`preset.get kind=workflow`): `{ data: { steps: [{ step, enabled?, rerun? }] } }` — đọc cả `data`,
 *   mảng trực tiếp, chuỗi, hoặc `{ key | step | name, enabled?, bucket? }`; gate là bước có tên `gate:<tên>`.
 * - trạng thái chạy: `sequence.pipeline = { run: { workflow, status, waiting, steps: [{ name, enabled, status }] },
 *   memory }` hoặc bản tóm tắt `pipeline.status` / `sequence.status.pipeline` (`{ status, waiting, steps: [...] }`).
 *   Gate đang chờ = `status === 'waiting_review'` + `waiting.gate`; bước chờ nhập = `waiting_input` + `waiting.step`.
 *   Vẫn đọc dạng map `{ steps: { <key>: StepState } }` (StepState = chuỗi hoặc `{ status, progress?, done?, total?,
 *   failed?, stale? }`).
 * - `sequence.gates`: quyết định duyệt `{ <tên>: { status: pending | approved | rejected, by, at } }`.
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

function presetSteps(preset: unknown): unknown[] | null {
  if (Array.isArray(preset)) return preset;
  if (!isObj(preset)) return null;
  if (Array.isArray(preset.steps)) return preset.steps;
  if (isObj(preset.data)) return presetSteps(preset.data);
  return null;
}

function stepName(item: Obj): string | null {
  for (const k of ['key', 'step', 'name'] as const) if (typeof item[k] === 'string') return item[k] as string;
  return null;
}

function normalizeWorkflow(preset: unknown): NormStep[] {
  const raw = presetSteps(preset);
  const list: unknown[] = raw && raw.length > 0 ? raw : DEFAULT_WORKFLOW;
  const out: NormStep[] = [];
  for (const item of list) {
    if (typeof item === 'string') {
      // spec viết `auto_select|gate:storyboard` = một trong hai: tách thành hai bước.
      for (const k of item.split('|')) if (k.trim()) out.push({ key: k.trim(), enabled: true, bucket: null });
    } else if (isObj(item) && stepName(item)) {
      const bucket = typeof item.bucket === 'string' && STAGE_KEYS.has(item.bucket) ? (item.bucket as StageKey) : null;
      out.push({ key: stepName(item)!, enabled: item.enabled !== false, bucket });
    }
  }
  return out.filter((s) => !PROJECT_LEVEL.has(s.key));
}

/** Trạng thái chạy đã chuẩn hoá. */
export interface RunView {
  /** idle · queued · running · waiting_review · waiting_input · pending_release · failed · cancelled · done */
  status: string;
  /** Gate đang chờ duyệt (`gate:<tên>`), null nếu không. */
  waitingGate: string | null;
  /** Bước đang chờ nhập, null nếu không. */
  waitingStep: string | null;
  /** Bước của lần chạy theo thứ tự (đã gồm bật/tắt ghi đè); null nếu là dạng map. */
  list: { name: string; enabled: boolean; status: string }[] | null;
  /** Trạng thái theo tên bước. */
  steps: Record<string, Obj>;
}

/**
 * Chuẩn hoá trạng thái chạy từ `sequence.pipeline` (`{run, memory}`), bản tóm tắt `pipeline.status`, hoặc dạng map.
 * Trả null khi chưa có lần chạy nào.
 */
export function normalizeRun(src: unknown): RunView | null {
  if (!isObj(src)) return null;
  const hasRun = isObj(src.run);
  const run: Obj = hasRun ? (src.run as Obj) : src;
  const status = typeof run.status === 'string' ? run.status : '';
  const waiting = isObj(run.waiting) ? run.waiting : null;
  const gateRaw = waiting && typeof waiting.gate === 'string' ? waiting.gate : null;
  const out: RunView = {
    status,
    waitingGate: status === 'waiting_review' && gateRaw ? (gateRaw.startsWith('gate:') ? gateRaw : `gate:${gateRaw}`) : null,
    waitingStep: status === 'waiting_input' && waiting && typeof waiting.step === 'string' ? waiting.step : null,
    list: null,
    steps: {},
  };
  if (Array.isArray(run.steps)) {
    out.list = [];
    for (const x of run.steps) {
      if (!isObj(x) || typeof x.name !== 'string') continue;
      out.list.push({ name: x.name, enabled: x.enabled !== false, status: String(x.status ?? '') });
      out.steps[x.name] = x;
    }
    if (out.list.length === 0) out.list = null;
  } else if (!hasRun && !('memory' in src)) {
    // Dạng map: `{steps: {k: v}}` hoặc `{k: v}` (`{memory}` không có run = chưa chạy).
    const map = isObj(run.steps) ? run.steps : run;
    for (const [k, v] of Object.entries(map)) {
      if (k === 'status' || k === 'waiting') continue;
      if (typeof v === 'string') out.steps[k] = { status: v };
      else if (isObj(v)) out.steps[k] = v;
    }
  }
  if (Object.keys(out.steps).length === 0 && !out.waitingGate && !out.waitingStep) return null;
  return out;
}

function stepState(run: RunView | null, key: string): Obj | null {
  return run?.steps[key] ?? null;
}

const RUNNING = new Set(['running', 'queued', 'in_progress', 'started']);
const DONE = new Set(['done', 'succeeded', 'success', 'complete', 'completed', 'ok']);
const FAILED = new Set(['failed', 'error', 'needs_attention', 'interrupted']);
const WAITING = new Set(['waiting', 'waiting_review', 'waiting_input', 'open', 'pending_review', 'awaiting']);
const SKIPPED = new Set(['skipped', 'disabled', 'off']);

function gateState(gates: unknown, run: RunView | null, key: string): { status: GateStatus; enabled: boolean } | null {
  const short = key.replace(/^gate:/, '');
  // Lần chạy đang dừng ở gate này: thắng mọi quyết định cũ trong `sequence.gates`.
  if (run?.waitingGate === key) return { status: 'waiting', enabled: true };
  const step = stepState(run, key);
  let v: unknown = isObj(gates) ? (gates[key] ?? gates[short]) : undefined;
  // Bước gate của lần chạy mới hơn quyết định đã lưu — trừ khi bước còn "pending" (chưa tới).
  if (step && (v === undefined || !['', 'pending'].includes(String(step.status ?? '')))) v = step;
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
  const run = normalizeRun(pipeline);
  // Lần chạy có danh sách bước (đã gồm bật/tắt ghi đè) thắng preset; không có cả hai → workflow mặc định.
  const steps = normalizeWorkflow(run?.list ?? workflowPreset ?? null);
  const byStage = new Map<StageKey, NormStep[]>(STAGE_DEFS.map((d) => [d.key, []]));
  let prev: StageKey = 'script';
  for (const s of steps) {
    const stage = s.bucket ?? STAGE_OF_STEP.get(s.key) ?? prev;
    byStage.get(stage)!.push(s);
    prev = stage;
  }
  const hasPipeline = run != null && Object.keys(run.steps).length > 0;

  return STAGE_DEFS.map((def, i) => {
    const mine = byStage.get(def.key)!;
    const work = mine.filter((s) => !s.key.startsWith('gate:'));
    const gateSteps = mine.filter((s) => s.key.startsWith('gate:'));

    // Gate: gate cuối cùng còn bật của chặng; gate đang chờ thắng.
    let gate: StageGate | null = null;
    for (const g of gateSteps) {
      const st = gateState(gates, run, g.key);
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
      const states = enabledWork.map((s) => stepState(run, s.key));
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
    else if (run?.waitingStep && mine.some((s) => s.key === run.waitingStep)) status = 'wait';
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
  const run = normalizeRun(pipeline);
  const keys = new Set<string>();
  if (isObj(gates)) for (const k of Object.keys(gates)) keys.add(k.startsWith('gate:') ? k : `gate:${k}`);
  if (run) for (const k of Object.keys(run.steps)) if (k.startsWith('gate:')) keys.add(k);
  if (run?.waitingGate) keys.add(run.waitingGate);
  let n = 0;
  for (const k of keys) {
    const st = gateState(gates, run, k);
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
