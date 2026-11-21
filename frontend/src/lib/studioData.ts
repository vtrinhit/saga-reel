/**
 * Dữ liệu của Studio, Âm thanh và Xuất (Task I): kiểu kết quả op (schema sinh ra trả `unknown` cho các op dạng dict),
 * khoá query dưới `['studio', …]`, hook đọc + làm mới sống theo `entity.changed`/`job.updated`, lỗi tiếng Việt.
 */
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef } from 'react';
import { ApiError } from '../api/client';
import { callOp } from '../api/ops';
import type { ProjectInfo, SequenceInfo } from '../api/types';
import { toast } from '../stores/toast';
import { useServerEvent } from './eventHub';
import type { GeneratedMeta } from './exportMatrix';
import type { ClipRow, DialogueRow, TimelineItemRow } from './timeline';

// --- Kiểu -----------------------------------------------------------------------------------------------------------

export interface SequenceRow extends SequenceInfo {
  settings?: Record<string, unknown>;
  target_platforms?: string[];
}

export interface ProjectRow extends ProjectInfo {
  languages?: string[];
  resolution?: string;
  fps?: number;
  updated_at?: string;
}

export interface PresetRow<D = Record<string, unknown>> {
  id: number;
  kind: string;
  key: string;
  name: string;
  data: D;
  builtin?: boolean;
  project_id?: number | null;
}

export interface PlatformData {
  label?: string;
  aspect?: string;
  width?: number;
  height?: number;
  duration_s?: { min?: number | null; max?: number | null; recommended?: number[] | null };
  safe_zone_px?: { top?: number; bottom?: number; left?: number; right?: number } | null;
  safe_zone_ref?: { width?: number; height?: number } | null;
  title?: { max_chars?: number | null; max_bytes?: number | null };
  caption?: { max_chars?: number | null; max_bytes?: number | null; max_utf16?: number | null };
  description?: { max_chars?: number | null; max_bytes?: number | null };
  hashtags?: { max?: number | null; max_shown?: number | null };
  ai_label?: { required?: boolean; mechanism?: string };
  max_size_mb?: number | null;
}

export interface RenderRow {
  id: number;
  sequence_id: number;
  variant: string;
  platform: string;
  lang: string;
  status: string;
  media_id: number | null;
  job_id: number | null;
  duration_ms: number | null;
  meta: Record<string, unknown>;
  url: string | null;
  created_at: string | null;
}

export interface VoiceProfile {
  id: number;
  project_id: number | null;
  key: string;
  name: string;
  connection: string;
  voice: string;
  rate: string;
  pitch: string;
  volume: string;
  style: string;
  per_lang: Record<string, { voice?: string; connection?: string; rate?: string; pitch?: string }>;
  updated_at?: string;
}

export interface SoundRow {
  id: number;
  project_id: number | null;
  media_id: number;
  kind: 'music' | 'sfx' | 'ambience';
  title: string;
  tags: string[];
  source: string;
  license: string;
  attribution: string;
}

export interface AssetBrief {
  id: number;
  key: string;
  name: string;
  kind: string;
  voice_profile_id?: number | null;
  updated_at?: string;
}

export interface Issue {
  code: string;
  message: string;
  hint?: string;
  [k: string]: unknown;
}

export interface CheckReport {
  errors: Issue[];
  warnings: Issue[];
  info?: { duration_ms?: number; size?: number; estimated?: boolean; platform?: string; lang?: string };
  /** Metadata đăng bài sẽ sinh ra khi xuất (mẫu, hoặc bản ghi đè gửi kèm `metadata`). */
  metadata?: GeneratedMeta;
}

export interface ExportRow {
  id: number;
  project_id: number;
  sequence_ids: number[];
  platform: string;
  lang: string;
  folder: string;
  status: string;
  checks: Record<string, unknown>;
  metadata: Record<string, unknown>;
  created_at: string | null;
}

export interface DurationPlanItem {
  clip_id: number;
  key: string;
  current_ms: number;
  proposed_ms: number;
  voice_ms: number | null;
  atempo?: number | null;
  video_speed?: number | null;
  hold_last_frame_ms?: number | null;
  ai_duration_s?: number | null;
  engine?: string;
  warnings: Issue[];
}

export interface DurationPlan {
  sequence_id: number;
  mode: string;
  ai_durations: number[];
  items: DurationPlanItem[];
  warnings: (Issue & { clip_id?: number })[];
}

// --- Lỗi ------------------------------------------------------------------------------------------------------------

/** "Không lưu được thời lượng Clip 3: <lý do>. <gợi ý>" */
export function errorText(what: string, e: unknown): string {
  if (e instanceof ApiError) return `${what}: ${e.detail}${e.hint ? ` ${e.hint}` : ''}`;
  if (e instanceof Error && e.message) return `${what}: ${e.message}`;
  return `${what}.`;
}

export function toastError(what: string, e: unknown, retry?: () => void): void {
  toast({ tone: 'error', message: errorText(what, e), action: retry ? { label: 'Thử lại', onClick: retry } : undefined });
}

/**
 * Hoàn tác: đúng change set của thao tác (`change_set_id` mà op ghi trả về) nếu có; không có thì thao tác gần nhất của
 * chính bạn trong dự án (`history.undo` không chỉ định change set).
 */
export async function undoLast(projectId: number, changeSetId?: number | null): Promise<void> {
  try {
    await callOp('history.undo', { project_id: projectId, change_set_id: changeSetId ?? null });
    toast({ tone: 'success', message: 'Đã hoàn tác.' });
  } catch (e) {
    toastError('Không hoàn tác được', e);
  }
}

// --- Khoá + hook ----------------------------------------------------------------------------------------------------

export const sk = {
  all: ['studio'] as const,
  project: (id: number) => ['studio', 'project', id] as const,
  sequence: (id: number) => ['studio', 'sequence', id] as const,
  sequences: (pid: number) => ['studio', 'sequences', pid] as const,
  clips: (id: number) => ['studio', 'clips', id] as const,
  lines: (id: number) => ['studio', 'lines', id] as const,
  items: (id: number) => ['studio', 'items', id] as const,
  renders: (id: number) => ['studio', 'renders', id] as const,
  presets: (kind: string, pid: number) => ['studio', 'preset', kind, pid] as const,
  voices: (pid: number) => ['studio', 'voices', pid] as const,
  sounds: (pid: number) => ['studio', 'sounds', pid] as const,
  assets: (pid: number) => ['studio', 'assets', pid] as const,
  exports: (pid: number) => ['studio', 'exports', pid] as const,
  check: (seq: number, platform: string, lang: string) => ['studio', 'check', seq, platform, lang] as const,
  ttsJobs: (pid: number) => ['studio', 'tts-jobs', pid] as const,
};

export const numId = (v: string | null | undefined): number | null => (v && /^\d+$/.test(v) ? Number(v) : null);

/** Làm mới dữ liệu Studio khi server báo thay đổi (gộp 400ms). Kiểm tra xuất (`check`) không tự chạy lại — tốn ffmpeg. */
export function useStudioLiveRefresh(): void {
  const qc = useQueryClient();
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const kick = () => {
    if (timer.current) return;
    timer.current = setTimeout(() => {
      timer.current = null;
      void qc.invalidateQueries({
        predicate: (q) => q.queryKey[0] === 'studio' && q.queryKey[1] !== 'check',
      });
    }, 400);
  };
  useServerEvent('entity.changed', kick);
  useServerEvent('resync', kick);
  const jobTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useServerEvent('job.updated', () => {
    if (jobTimer.current) return;
    jobTimer.current = setTimeout(() => {
      jobTimer.current = null;
      for (const k of ['renders', 'exports', 'tts-jobs', 'lines']) void qc.invalidateQueries({ queryKey: ['studio', k] });
    }, 1500);
  });
}

export function useProjectRow(pid: number | null) {
  return useQuery({
    queryKey: sk.project(pid ?? -1),
    queryFn: async () => (await callOp('project.get', { id: pid! })) as unknown as ProjectRow,
    enabled: pid != null,
    staleTime: 30_000,
  });
}

export function useSequenceRow(id: number | null) {
  return useQuery({
    queryKey: sk.sequence(id ?? -1),
    queryFn: async () => (await callOp('sequence.get', { id: id! })) as unknown as SequenceRow,
    enabled: id != null,
  });
}

export function useSequences(pid: number | null) {
  return useQuery({
    queryKey: sk.sequences(pid ?? -1),
    queryFn: async () => {
      const all: SequenceRow[] = [];
      let cursor: string | null = null;
      do {
        const page = (await callOp('sequence.list', { project_id: pid!, cursor, limit: 500 })) as unknown as {
          items: SequenceRow[];
          next_cursor: string | null;
        };
        all.push(...page.items);
        cursor = page.next_cursor;
      } while (cursor);
      return all;
    },
    enabled: pid != null,
    staleTime: 30_000,
  });
}

/** Toàn bộ clip của tập theo thứ tự (đọc hết các trang 500). */
export function useClips(seqId: number | null) {
  return useQuery({
    queryKey: sk.clips(seqId ?? -1),
    queryFn: async () => {
      const all: ClipRow[] = [];
      let cursor: string | null = null;
      do {
        const page = (await callOp('clip.list', { sequence_id: seqId!, cursor, limit: 500 })) as unknown as {
          items: ClipRow[];
          next_cursor: string | null;
        };
        all.push(...page.items);
        cursor = page.next_cursor;
      } while (cursor);
      return all;
    },
    enabled: seqId != null,
  });
}

export function useLines(seqId: number | null) {
  return useQuery({
    queryKey: sk.lines(seqId ?? -1),
    queryFn: async () => ((await callOp('dialogue.list', { sequence_id: seqId! })) as unknown as { items: DialogueRow[] }).items,
    enabled: seqId != null,
  });
}

export function useTimelineItems(seqId: number | null) {
  return useQuery({
    queryKey: sk.items(seqId ?? -1),
    queryFn: async () =>
      ((await callOp('timeline.list', { sequence_id: seqId! })) as unknown as { items: TimelineItemRow[] }).items,
    enabled: seqId != null,
  });
}

export function useRenders(seqId: number | null) {
  return useQuery({
    queryKey: sk.renders(seqId ?? -1),
    queryFn: async () =>
      ((await callOp('render.list', { sequence_id: seqId!, limit: 20 })) as unknown as { items: RenderRow[] }).items,
    enabled: seqId != null,
  });
}

export function usePresets<D = Record<string, unknown>>(kind: string, pid: number | null) {
  return useQuery({
    queryKey: sk.presets(kind, pid ?? -1),
    queryFn: async () => ((await callOp('preset.list', { kind: kind as never, project_id: pid })) as unknown as { items: PresetRow<D>[] }).items,
    enabled: pid != null,
    staleTime: 5 * 60_000,
  });
}

export function useVoices(pid: number | null) {
  return useQuery({
    queryKey: sk.voices(pid ?? -1),
    queryFn: async () => ((await callOp('voice.list', { project_id: pid!, include_global: true })) as unknown as { items: VoiceProfile[] }).items,
    enabled: pid != null,
  });
}

export function useSounds(pid: number | null) {
  return useQuery({
    queryKey: sk.sounds(pid ?? -1),
    queryFn: async () => ((await callOp('sound.list', { project_id: pid!, include_global: true })) as unknown as { items: SoundRow[] }).items,
    enabled: pid != null,
  });
}

export function useAssets(pid: number | null) {
  return useQuery({
    queryKey: sk.assets(pid ?? -1),
    queryFn: async () =>
      ((await callOp('asset.list', { project_id: pid!, limit: 500 })) as unknown as { items: AssetBrief[] }).items,
    enabled: pid != null,
    staleTime: 60_000,
  });
}

export function useExports(pid: number | null) {
  return useQuery({
    queryKey: sk.exports(pid ?? -1),
    queryFn: async () =>
      ((await callOp('export.list', { project_id: pid, limit: 100 })) as unknown as { items: ExportRow[] }).items,
    enabled: pid != null,
  });
}

/**
 * Độ dài nguồn của video đang chọn cho từng clip có video (`clip.list` → `source_duration_ms`) — để timeline dài theo
 * nguồn như `normalize.plan` và trim ghi `trim_out_ms`. Hàm thuần, không gọi op.
 */
export function videoSources(clips: ClipRow[]): Map<number, number> {
  const out = new Map<number, number>();
  for (const c of clips) {
    const v = c.source_duration_ms;
    if (c.video_take_id != null && typeof v === 'number' && v > 0) out.set(c.id, v);
  }
  return out;
}

/**
 * Clip nào bản dựng giữ nguyên độ dài video nguồn (để timeline dài đúng như bản dựng — `render/pipeline.py`):
 * `ai_video` được khớp về `clip.duration_ms` (dài → cắt, ngắn → chậm/giữ khung cuối) TRỪ khi clip kế nối `continue`
 * (giữ nguyên nguồn, cảnh báo `continue_untrimmed`); engine khác (`still_motion`, `code_anim`) theo nguồn. Clip còn lại
 * dài theo `duration_ms`; độ dài nguồn chỉ dùng làm trần khi kéo dài/cắt.
 */
export function renderSourceMs(clips: ClipRow[], sources: Map<number, number>): Map<number, number> {
  const out = new Map<number, number>();
  clips.forEach((c, i) => {
    const src = sources.get(c.id);
    if (src == null) return;
    const nextContinue = clips[i + 1]?.link === 'continue';
    if (c.engine !== 'ai_video' || autoTrimActive(c) || c.trim_mode === 'none' || (nextContinue && src > (c.duration_ms ?? 0)))
      out.set(c.id, src);
  });
  return out;
}

/** Bản dựng đang tự cắt clip này (chế độ auto + có đề xuất áp dụng được) — `render/pipeline.py`. */
export function autoTrimActive(c: Pick<ClipRow, 'trim_mode' | 'auto_trim'>): boolean {
  return (c.trim_mode ?? 'auto') === 'auto' && !!c.auto_trim?.applied;
}

/**
 * Clip với trim hiệu lực như bản dựng: auto → trim theo `auto_trim`; none → nguyên take. Dùng để dựng timeline
 * (độ dài = nguồn − trim) cho khớp bản dựng. Hàm thuần.
 */
export function effectiveClips(clips: ClipRow[]): ClipRow[] {
  return clips.map((c) => {
    if (autoTrimActive(c)) return { ...c, trim_in_ms: c.auto_trim!.in_ms, trim_out_ms: c.auto_trim!.out_ms };
    if (c.trim_mode === 'none') return { ...c, trim_in_ms: 0, trim_out_ms: 0 };
    return c;
  });
}

/** Sửa trim tay trên clip đang tự cắt → chuyển sang thủ công, giữ điểm vào của đề xuất. */
export function manualTrimPatch(c: ClipRow, values: Record<string, unknown>): Record<string, unknown> {
  if (!autoTrimActive(c) || !('trim_out_ms' in values || 'trim_in_ms' in values)) return values;
  return { trim_mode: 'manual', trim_in_ms: c.auto_trim!.in_ms, trim_out_ms: c.auto_trim!.out_ms, ...values };
}

export const TRIM_REASON: Record<string, string> = {
  speech: 'theo đoạn nói (0,2 s trước tiếng đầu → 0,3 s sau tiếng cuối)',
  motion: 'bỏ đầu/đuôi đứng yên, giữ độ dài mong muốn',
  static: 'video đứng yên: cắt đuôi theo độ dài mong muốn',
  voice: 'clip lồng tiếng: độ dài theo giọng đọc',
  continue: 'clip sau nối tiếp từ khung cuối: không cắt đuôi',
  short: 'video không dài hơn độ dài mong muốn',
  not_analyzed: 'chưa phân tích video',
  manual: 'trim tay',
  none: 'nguyên take',
};

/** Bản dựng xem được mới nhất (ưu tiên đủ tập hơn bản xem trước N giây cùng lúc). */
export function latestPlayable(renders: RenderRow[] | undefined): RenderRow | null {
  const ok = (renders ?? []).filter((r) => r.status === 'succeeded' && r.url);
  return ok[0] ?? null;
}

/** Render đang chạy (job chưa xong) mới nhất. */
export function activeRender(renders: RenderRow[] | undefined): RenderRow | null {
  return (renders ?? []).find((r) => r.status === 'pending' || r.status === 'running' || r.status === 'queued') ?? null;
}

export const VARIANT_LABEL: Record<string, string> = { draft: 'Bản nháp', animatic: 'Bản animatic', final: 'Bản cuối' };

/**
 * Gate quy trình đang chờ bạn duyệt (tên không tiền tố) — đọc `sequence.pipeline.run`: `status === 'waiting_review'` +
 * `waiting.gate` (`services/pipeline/engine.py`). `sequence.gates` chỉ lưu quyết định (pending/approved/rejected).
 */
export function waitingGate(pipeline: unknown): string | null {
  if (!pipeline || typeof pipeline !== 'object') return null;
  const run = (pipeline as { run?: unknown }).run;
  if (!run || typeof run !== 'object') return null;
  const r = run as { status?: unknown; waiting?: { gate?: unknown } | null };
  const g = r.status === 'waiting_review' ? r.waiting?.gate : null;
  return typeof g === 'string' && g ? g.replace(/^gate:/, '') : null;
}

export const GATE_LABEL: Record<string, string> = {
  outline: 'Duyệt dàn ý',
  audio_plan: 'Duyệt kế hoạch thoại',
  storyboard: 'Duyệt khung hình',
  animatic: 'Duyệt animatic',
  pilot: 'Duyệt thử 2 clip',
  preview: 'Duyệt bản xem trước',
  final: 'Duyệt bản cuối',
};
