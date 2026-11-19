/**
 * Cần bạn / Hoạt động (Task M): kiểu kết quả op quy trình + hàm thuần dựng danh sách việc chờ người.
 *
 * Nguồn sự thật (backend `services/pipeline/engine.py::_summary`): `pipeline.status` trả mỗi tập một bản tóm tắt
 * `{status, waiting: {gate}|{step,…}, steps: [{name, status, enabled}], gates: {<tên>: {status, by, at, …}}}`.
 * Gate đang chờ = `status === 'waiting_review'` và `waiting.gate`; chờ nhập (kịch bản/prompt do bạn hoặc agent nộp) =
 * `waiting_input`.
 */
import type { BudgetStatus, Job } from '../api/types';
import { STAGE_DEFS, type StageKey } from './pipeline';
import { parseServerTime } from './time';

/* ------------------------------------------------------------------------------------------------------------------ */
/* Kiểu kết quả op (viết tay theo backend — giữ trong khu M để không đụng `api/types.ts`)                             */
/* ------------------------------------------------------------------------------------------------------------------ */

export interface PipelineStep {
  name: string;
  label: string;
  enabled: boolean;
  status: string;
  attempts?: number;
  feedback?: string;
  warnings?: unknown[];
  error?: { code?: string; message?: string; hint?: string } | string | null;
}

/** `pipeline.status` → `items[]` (`engine._summary`). */
export interface PipelineSummary {
  sequence_id: number;
  number: number;
  title: string;
  workflow: string | null;
  /** idle · queued · running · waiting_review · waiting_input · pending_release · failed · cancelled · done */
  status: string;
  job_id: number | null;
  current: string | null;
  waiting: { gate?: string; step?: string; [k: string]: unknown } | null;
  error: { code?: string; message?: string; hint?: string } | string | null;
  steps: PipelineStep[];
  gates: Record<string, { status?: string; by?: string; at?: string; feedback?: string; count?: number } | undefined>;
  progress: number;
  updated_at: string | null;
}

/** `pipeline.run` dry_run (`engine.plan`). */
export interface PipelinePlan {
  dry_run: true;
  workflow: string;
  steps: string[];
  sequences: { sequence_id: number; clips: number; images: number; videos: number; llm_calls: number; status: string }[];
  estimate: { images: number; videos: number; llm_calls: number; budget: BudgetStatus };
  note?: string;
}

/** `sequence.status` (`engine.sequence_status`). */
export interface SequenceStatus {
  sequence_id: number;
  number: number;
  title: string;
  has_script: boolean;
  clips: {
    total: number;
    with_image: number;
    with_video: number;
    image_prompt: number;
    motion_prompt: number;
    approved: number;
    flagged: number;
    seconds: number;
  };
  active_jobs: number;
  cost?: Record<string, unknown>;
}

/* ------------------------------------------------------------------------------------------------------------------ */
/* Tên gate, bước, chặng                                                                                              */
/* ------------------------------------------------------------------------------------------------------------------ */

const GATE_LABEL: Record<string, string> = {
  outline: 'dàn ý',
  audio_plan: 'kế hoạch thoại',
  storyboard: 'khung hình',
  animatic: 'animatic',
  pilot: 'tập thử',
  preview: 'bản xem trước',
  final: 'bản cuối',
};

/** "animatic" · "gate:final" → "bản cuối"; gate tự đặt giữ nguyên tên. */
export function gateLabel(gate: string): string {
  const short = gate.replace(/^gate:/, '');
  return GATE_LABEL[short] ?? short.replace(/_/g, ' ');
}

/** "Duyệt animatic · Tập 12 · Đêm trăng máu" */
export function gateTitle(gate: string, seq: { number: number; title: string }): string {
  const name = seq.title && seq.title !== `Tập ${seq.number}` ? ` · ${seq.title}` : '';
  return `Duyệt ${gateLabel(gate)} · Tập ${seq.number}${name}`;
}

const STAGE_OF_STEP = new Map<string, { key: StageKey; label: string }>();
for (const d of STAGE_DEFS) for (const s of [...d.steps, ...d.gates]) STAGE_OF_STEP.set(s, { key: d.key, label: d.label });

export function stageOfStep(step: string): { key: StageKey; label: string } | null {
  return STAGE_OF_STEP.get(step) ?? null;
}

const COST_STEPS: Record<string, 'images' | 'videos' | 'llm'> = {
  generate_images: 'images',
  generate_videos: 'videos',
  outline: 'llm',
  write_script: 'llm',
  split_clips: 'llm',
  image_prompts: 'llm',
  motion_prompts: 'llm',
  dialogue: 'llm',
  memory_summarize: 'llm',
};

export interface NextSegment {
  /** Bước bật (chưa xong) sau gate tới gate kế tiếp. */
  steps: string[];
  /** Chặng hiển thị của bước đầu tiên (vd "Video"). */
  stage: { key: StageKey; label: string } | null;
  /** Gate kế tiếp sẽ dừng lại (null = chạy tới hết). */
  nextGate: string | null;
  /** Loại chi phí mà đoạn này tiêu. */
  spends: { images: boolean; videos: boolean; llm: boolean };
}

/** Phần sẽ chạy nếu duyệt `gate`: các bước bật, chưa xong, sau gate cho tới gate bật kế tiếp. */
export function nextSegment(steps: PipelineStep[], gate: string): NextSegment {
  const name = gate.startsWith('gate:') ? gate : `gate:${gate}`;
  const idx = steps.findIndex((s) => s.name === name);
  const out: NextSegment = { steps: [], stage: null, nextGate: null, spends: { images: false, videos: false, llm: false } };
  if (idx < 0) return out;
  for (const s of steps.slice(idx + 1)) {
    if (!s.enabled || s.status === 'done' || s.status === 'skipped') continue;
    if (s.name.startsWith('gate:')) {
      out.nextGate = s.name;
      break;
    }
    out.steps.push(s.name);
    const k = COST_STEPS[s.name];
    if (k) out.spends[k] = true;
  }
  out.stage = out.steps.length ? stageOfStep(out.steps[0]) : null;
  return out;
}

export interface NextCostView {
  /** "Nếu duyệt, chạy tiếp chặng Video" */
  lead: string;
  /** "48 video · 0 lượt" — null khi chưa có ước tính. */
  amount: string | null;
  /** Ghi chú: ngân sách còn, gate kế tiếp. */
  notes: string[];
  overBudget: boolean;
  /** Tổng lượt tính vào ngân sách (ảnh + video có bật ngân sách). */
  credits: number;
}

const nf = new Intl.NumberFormat('vi-VN');

/**
 * Khối "giá bước kế" của GateCard. `plan` = dry_run của tập (ước tính toàn bộ phần còn lại của workflow — chỉ lấy loại
 * mà đoạn kế tiếp thật sự tiêu). Ngân sách: kind có `enabled` mới tính lượt; tắt = "không giới hạn".
 */
export function nextCost(seg: NextSegment, plan: PipelinePlan['sequences'][number] | null, budget: BudgetStatus | null): NextCostView {
  const lead = seg.stage ? `Nếu duyệt, chạy tiếp chặng ${seg.stage.label}` : 'Nếu duyệt, quy trình chạy tới hết';
  const notes: string[] = [];
  if (seg.nextGate) notes.push(`dừng lại ở gate ${gateLabel(seg.nextGate)}`);
  if (!plan) return { lead, amount: null, notes, overBudget: false, credits: 0 };
  const parts: string[] = [];
  let credits = 0;
  let overBudget = false;
  for (const kind of ['images', 'videos'] as const) {
    if (!seg.spends[kind]) continue;
    const n = plan[kind] ?? 0;
    const bk = budget?.[kind === 'images' ? 'image' : 'video'];
    parts.push(`${nf.format(n)} ${kind === 'images' ? 'ảnh' : 'video'}`);
    if (bk?.enabled) {
      credits += n;
      if (bk.remaining != null) {
        notes.push(`ngân sách ${kind === 'images' ? 'ảnh' : 'video'} còn ${nf.format(bk.remaining)} lượt`);
        if (n > bk.remaining) overBudget = true;
      }
    } else if (n > 0) {
      notes.push(`${kind === 'images' ? 'ảnh' : 'video'} không giới hạn ngân sách`);
    }
  }
  if (seg.spends.llm && plan.llm_calls > 0) parts.push(`${nf.format(plan.llm_calls)} lần gọi LLM`);
  const amount = parts.length ? `${parts.join(' · ')} · ${nf.format(credits)} lượt` : 'miễn phí · 0 lượt';
  return { lead, amount, notes, overBudget, credits };
}

/* ------------------------------------------------------------------------------------------------------------------ */
/* Danh sách "Cần bạn"                                                                                                 */
/* ------------------------------------------------------------------------------------------------------------------ */

export type NeedKind = 'gate' | 'input' | 'pipeline_failed' | 'job_failed' | 'job_attention' | 'budget';

export interface Need {
  id: string;
  kind: NeedKind;
  projectId: number | null;
  sequenceId: number | null;
  title: string;
  detail: string;
  /** Đường dẫn tới màn đã lọc. */
  href: string;
  /** ms epoch — sắp mới trước. */
  at: number;
  /** Gate: tên gate (không tiền tố). */
  gate?: string;
  summary?: PipelineSummary;
  jobIds?: number[];
}

const RANK: Record<NeedKind, number> = { gate: 0, input: 1, pipeline_failed: 2, job_attention: 3, job_failed: 4, budget: 5 };

function errText(e: PipelineSummary['error'] | Job['error_message']): string {
  if (!e) return '';
  if (typeof e === 'string') return e;
  return e.message || e.code || '';
}

function seqName(s: { number: number; title: string }): string {
  return s.title && s.title !== `Tập ${s.number}` ? `Tập ${s.number} · ${s.title}` : `Tập ${s.number}`;
}

/** Link tới màn công cụ của bước đang chờ nhập. */
function inputHref(base: string, s: PipelineSummary): string {
  const step = String(s.waiting?.step ?? s.current ?? '');
  const st = stageOfStep(step);
  if (st?.key === 'script') return `${base}/story?seq=${s.sequence_id}`;
  return `${base}/s/${s.sequence_id}`;
}

const JOB_ATTENTION = new Set(['needs_attention', 'interrupted']);

/**
 * Gom việc chờ người: gate mở, bước chờ nhập, quy trình lỗi, việc cần xử lý, việc lỗi, việc chờ vì vượt ngân sách
 * (gộp một dòng mỗi dự án). `projectName` (cho `/inbox`) thêm tên dự án vào chi tiết.
 */
export function buildNeeds(
  pipelines: PipelineSummary[],
  jobs: Job[],
  opts: { projectId: number | null; projectName?: (id: number | null) => string | null } = { projectId: null },
): Need[] {
  const out: Need[] = [];
  const pname = (id: number | null) => (opts.projectName ? opts.projectName(id) : null);
  const base = (pid: number | null) => (pid != null ? `/p/${pid}` : '/projects');
  const pidOfSeq = new Map<number, number | null>();
  for (const j of jobs) if (j.sequence_id != null) pidOfSeq.set(j.sequence_id, j.project_id);

  for (const s of pipelines) {
    const pid = (s as PipelineSummary & { project_id?: number }).project_id ?? opts.projectId ?? pidOfSeq.get(s.sequence_id) ?? null;
    const at = parseServerTime(s.waiting?.since) ?? parseServerTime(s.updated_at) ?? 0;
    const where = pname(pid);
    const suffix = where ? ` · ${where}` : '';
    if (s.status === 'waiting_review' && s.waiting?.gate) {
      out.push({
        id: `gate:${s.sequence_id}:${s.waiting.gate}`, kind: 'gate', projectId: pid, sequenceId: s.sequence_id,
        title: gateTitle(s.waiting.gate, s), detail: `Quy trình ${s.workflow ?? ''} dừng chờ bạn duyệt${suffix}`.replace('  ', ' '),
        href: `${base(pid)}/activity`, at, gate: s.waiting.gate, summary: s,
      });
    } else if (s.status === 'waiting_input') {
      const step = String(s.waiting?.step ?? s.current ?? '');
      const label = s.steps.find((x) => x.name === step)?.label ?? step;
      out.push({
        id: `input:${s.sequence_id}`, kind: 'input', projectId: pid, sequenceId: s.sequence_id,
        title: `${seqName(s)} chờ ${label.toLowerCase() || 'dữ liệu'}`,
        detail: `Bạn tự viết, hoặc để agent nộp qua prompts.submit${suffix}`,
        href: inputHref(base(pid), s), at, summary: s,
      });
    } else if (s.status === 'failed') {
      out.push({
        id: `pfail:${s.sequence_id}`, kind: 'pipeline_failed', projectId: pid, sequenceId: s.sequence_id,
        title: `Quy trình ${seqName(s)} dừng vì lỗi`,
        detail: (errText(s.error) || `Bước ${s.current ?? '?'} lỗi`) + suffix,
        href: `${base(pid)}/activity`, at, summary: s,
      });
    }
  }

  const budgetByProject = new Map<number | null, Job[]>();
  for (const j of jobs) {
    const at = parseServerTime(j.updated_at ?? j.created_at) ?? 0;
    const where = pname(j.project_id);
    const suffix = where ? ` · ${where}` : '';
    const scope = j.sequence_id != null ? ` · tập #${j.sequence_id}` : '';
    if (j.error_code === 'budget_exceeded' && !['succeeded', 'cancelled', 'failed'].includes(String(j.status))) {
      const list = budgetByProject.get(j.project_id) ?? [];
      list.push(j);
      budgetByProject.set(j.project_id, list);
      continue;
    }
    if (JOB_ATTENTION.has(String(j.status))) {
      out.push({
        id: `job:${j.id}`, kind: 'job_attention', projectId: j.project_id, sequenceId: j.sequence_id,
        title: j.status === 'interrupted' ? `Việc #${j.id} bị ngắt${scope}` : `Việc #${j.id} cần bạn quyết${scope}`,
        detail: (j.error_message || j.hint || 'Xem việc để chọn gửi lại hoặc bỏ.') + suffix,
        href: `/jobs?job=${j.id}`, at, jobIds: [j.id],
      });
    } else if (j.status === 'failed') {
      out.push({
        id: `job:${j.id}`, kind: 'job_failed', projectId: j.project_id, sequenceId: j.sequence_id,
        title: `Việc #${j.id} lỗi${scope}`,
        detail: (j.error_message || j.error_code || 'Không rõ lý do') + suffix,
        href: `/jobs?job=${j.id}`, at, jobIds: [j.id],
      });
    }
  }
  for (const [pid, list] of budgetByProject) {
    const where = pname(pid);
    out.push({
      id: `budget:${pid ?? 'all'}`, kind: 'budget', projectId: pid, sequenceId: null,
      title: `${nf.format(list.length)} việc chờ vì vượt ngân sách tháng`,
      detail: `Nâng ngân sách hoặc huỷ việc${where ? ` · ${where}` : ''}`,
      href: '/settings/budget', at: Math.max(...list.map((j) => parseServerTime(j.updated_at ?? j.created_at) ?? 0)),
      jobIds: list.map((j) => j.id),
    });
  }
  return out.sort((a, b) => RANK[a.kind] - RANK[b.kind] || b.at - a.at);
}

/** Câu tóm tắt đầu trang: "5 việc chờ quyết định · 2 agent đang làm · 12 việc đang chạy". */
export function activitySummary(needs: number, agents: number, running: number): string {
  const parts = [
    needs ? `${nf.format(needs)} việc chờ quyết định` : 'Không có gì chờ bạn',
    agents ? `${nf.format(agents)} agent đang làm` : null,
    running ? `${nf.format(running)} việc đang chạy` : null,
  ].filter(Boolean);
  return parts.join(' · ');
}

/** Pipeline còn chạy (để hiện ở mục "Quy trình" kèm Huỷ). */
export const PIPELINE_ACTIVE = new Set(['queued', 'running', 'waiting_review', 'waiting_input', 'pending_release']);

const PIPE_STATUS: Record<string, string> = {
  idle: 'chưa chạy',
  queued: 'đang xếp hàng',
  running: 'đang chạy',
  waiting_review: 'chờ bạn duyệt',
  waiting_input: 'chờ nhập',
  pending_release: 'chờ tập dẫn',
  failed: 'lỗi',
  cancelled: 'đã huỷ',
  done: 'xong',
};

export function pipelineStatusText(s: string): string {
  return PIPE_STATUS[s] ?? s;
}

/** Câu lỗi tiếng Việt từ server: "chi tiết + gợi ý". */
export function errorMessage(e: unknown, fallback: string): string {
  const x = e as { detail?: unknown; hint?: unknown } | null;
  if (x && typeof x.detail === 'string' && x.detail) return `${x.detail}${typeof x.hint === 'string' && x.hint ? ` ${x.hint}` : ''}`;
  return fallback;
}

/* ------------------------------------------------------------------------------------------------------------------ */
/* Agent                                                                                                              */
/* ------------------------------------------------------------------------------------------------------------------ */

export interface AgentRow {
  actor: string;
  name: string;
  lastAt: number;
  changes: number;
  lastSummary: string;
  lastOp: string;
  /** Ghi trong 2 phút gần nhất. */
  live: boolean;
  /** Việc đang chạy do agent tạo (job.params.actor). */
  runningJobs: number;
}

/**
 * Agent có change set trong `windowMs` (mặc định 24 giờ) + việc đang chạy mang `params.actor` của agent. Sắp agent
 * đang ghi lên đầu, rồi mới nhất trước.
 */
export function agentRows(
  history: { actor: string; created_at: string; summary: string; op_name: string }[] | undefined,
  jobs: Pick<Job, 'status' | 'params'>[] | undefined,
  now: number = Date.now(),
  windowMs = 24 * 3600_000,
): AgentRow[] {
  const map = new Map<string, AgentRow>();
  const isAgent = (a: string) => a.startsWith('agent:');
  for (const cs of history ?? []) {
    if (!isAgent(cs.actor)) continue;
    const at = parseServerTime(cs.created_at);
    if (at == null || now - at > windowMs) continue;
    const cur = map.get(cs.actor);
    if (cur) {
      cur.changes += 1;
      if (at > cur.lastAt) Object.assign(cur, { lastAt: at, lastSummary: cs.summary, lastOp: cs.op_name });
    } else {
      map.set(cs.actor, {
        actor: cs.actor, name: cs.actor.slice(6), lastAt: at, changes: 1, lastSummary: cs.summary, lastOp: cs.op_name,
        live: false, runningJobs: 0,
      });
    }
  }
  for (const j of jobs ?? []) {
    const actor = typeof j.params?.actor === 'string' ? j.params.actor : null;
    if (!actor || !isAgent(actor) || !['queued', 'running'].includes(String(j.status))) continue;
    const cur = map.get(actor) ?? {
      actor, name: actor.slice(6), lastAt: 0, changes: 0, lastSummary: '', lastOp: '', live: false, runningJobs: 0,
    };
    cur.runningJobs += 1;
    map.set(actor, cur);
  }
  const out = [...map.values()];
  for (const a of out) a.live = a.runningJobs > 0 || (a.lastAt > 0 && now - a.lastAt <= 2 * 60_000);
  return out.sort((a, b) => Number(b.live) - Number(a.live) || b.lastAt - a.lastAt);
}
