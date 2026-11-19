/** Câu chuyện (Task M): kiểu Story Bible + chữ hiển thị + gộp chữ LLM chạy dần. Hàm thuần. */

export type BibleType = 'character' | 'location' | 'relationship' | 'event' | 'storyline' | 'fact' | 'open_question';
export type BibleStatus = 'pending' | 'confirmed' | 'rejected';

export interface BibleEvidence {
  id: number;
  item_id: number;
  sequence_id: number | null;
  source: 'script' | 'novel' | 'manual' | 'llm' | string;
  quote: string;
  start: number | null;
  end: number | null;
  created_at: string | null;
}

/** `bible.list` / `bible.get` / `bible.search` (`services/story/bible.py::item_dict`). */
export interface BibleItem {
  id: number;
  project_id: number;
  type: BibleType;
  key: string;
  summary: string;
  attributes: Record<string, unknown>;
  status: BibleStatus;
  locked: boolean;
  story_order: number | null;
  asset_id: number | null;
  evidence_count: number;
  evidence?: BibleEvidence[];
  score?: number;
  updated_at?: string;
}

export const BIBLE_TYPES: { value: BibleType; label: string }[] = [
  { value: 'character', label: 'nhân vật' },
  { value: 'location', label: 'bối cảnh' },
  { value: 'relationship', label: 'quan hệ' },
  { value: 'event', label: 'sự kiện' },
  { value: 'storyline', label: 'tuyến truyện' },
  { value: 'fact', label: 'sự thật' },
  { value: 'open_question', label: 'câu hỏi mở' },
];

const TYPE_LABEL = Object.fromEntries(BIBLE_TYPES.map((t) => [t.value, t.label])) as Record<string, string>;

export function bibleTypeLabel(t: string): string {
  return TYPE_LABEL[t] ?? t;
}

const SOURCE_LABEL: Record<string, string> = { script: 'kịch bản', novel: 'truyện gốc', manual: 'tự ghi', llm: 'LLM' };

/** "— kịch bản tập 3" */
export function evidenceSource(e: Pick<BibleEvidence, 'source' | 'sequence_id'>, seqNumber?: (id: number) => number | null): string {
  const src = SOURCE_LABEL[e.source] ?? e.source;
  const n = e.sequence_id != null ? (seqNumber?.(e.sequence_id) ?? null) : null;
  return n != null ? `${src} tập ${n}` : e.sequence_id != null ? `${src} · tập #${e.sequence_id}` : src;
}

/** Dòng meta: "sự thật · từ tập 3". */
export function bibleMeta(it: Pick<BibleItem, 'type' | 'story_order'>): string {
  return `${bibleTypeLabel(it.type)}${it.story_order != null ? ` · từ tập ${it.story_order}` : ''}`;
}

/** Sắp: chờ xác nhận trước (cần bạn), rồi theo loại, rồi theo tập. */
export function sortBible(items: BibleItem[]): BibleItem[] {
  const order = new Map(BIBLE_TYPES.map((t, i) => [t.value as string, i]));
  return [...items].sort(
    (a, b) =>
      Number(b.status === 'pending') - Number(a.status === 'pending') ||
      (order.get(a.type) ?? 99) - (order.get(b.type) ?? 99) ||
      (a.story_order ?? 0) - (b.story_order ?? 0) ||
      a.id - b.id,
  );
}

/* ------------------------------------------------------------------------------------------------------------------ */
/* Chữ chạy dần                                                                                                       */
/* ------------------------------------------------------------------------------------------------------------------ */

/** Sự kiện `llm.delta` = `{job_id, task, text}` — `text` là ĐOẠN MỚI (không phải toàn văn). */
export interface LlmDelta {
  job_id: number;
  task?: string;
  text: string;
}

export function asDelta(data: unknown): LlmDelta | null {
  const d = data as Partial<LlmDelta> | null;
  if (!d || typeof d.job_id !== 'number' || typeof d.text !== 'string') return null;
  return { job_id: d.job_id, task: d.task, text: d.text };
}

/** Bộ gộp theo job: thêm đoạn, đọc toàn văn; giới hạn bộ nhớ (giữ 200k ký tự cuối). */
export class DeltaBuffer {
  private parts = new Map<number, string>();
  private readonly max: number;
  constructor(max = 200_000) {
    this.max = max;
  }
  push(d: LlmDelta): string {
    const cur = (this.parts.get(d.job_id) ?? '') + d.text;
    const next = cur.length > this.max ? cur.slice(cur.length - this.max) : cur;
    this.parts.set(d.job_id, next);
    return next;
  }
  get(jobId: number): string {
    return this.parts.get(jobId) ?? '';
  }
  clear(jobId: number): void {
    this.parts.delete(jobId);
  }
}

/** Câu trọn vẹn cuối cùng (để `aria-live` đọc theo câu, không đọc từng cụm). */
export function lastSentence(text: string): string {
  const t = text.trimEnd();
  const m = t.match(/[^.!?…\n]*[.!?…](?=\s|$)(?![\s\S]*[.!?…](?=\s|$))/);
  return m ? m[0].trim() : '';
}

const TASK_LABEL: Record<string, string> = {
  'story.write_bible': 'Viết Story Bible',
  'story.outline': 'Lập dàn ý',
  'story.write_script': 'Viết kịch bản',
  'story.split_clips': 'Chia clip',
  'story.image_prompts': 'Prompt ảnh',
  'story.motion_prompts': 'Prompt chuyển động',
  'story.dialogue': 'Lời thoại',
  'story.adapt_novel': 'Chuyển thể truyện',
  'story.memory_summarize': 'Ghi bộ nhớ truyện',
};

/** `story.run` có `params.task = write_script` → "Viết kịch bản". */
export function storyTaskLabel(task: string | undefined | null): string {
  if (!task) return 'Viết';
  const k = task.startsWith('story.') ? task : `story.${task}`;
  return TASK_LABEL[k] ?? task;
}
