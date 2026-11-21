/**
 * Prompt của dự án (`prompt.*`): hợp đồng của tool (`prompt.contracts`), danh sách khoá của dự án, xem trước.
 * Prompt là dữ liệu của từng dự án — không có bộ prompt chung của tool.
 */

export interface PromptVariable {
  name: string;
  description: string;
  required: boolean;
}

/** `prompt.contracts` → `compile/prompt_contracts.py::to_dict`. */
export interface PromptContract {
  key: string;
  kind: 'compile' | 'llm';
  group: 'compile' | 'story' | 'check';
  group_label: string;
  label: string;
  purpose: string;
  variables: PromptVariable[];
  json: boolean;
  output: string;
  output_schema: Record<string, unknown> | null;
  /** Khối định dạng đầu ra tool tự nối khi chạy bằng LLM của tool (prompt dự án chỉ giữ phần sáng tạo). */
  output_format: string;
  used_by: string[];
}

export interface PromptItem {
  id: number;
  project_id: number;
  key: string;
  label: string;
  group: string;
  kind: string;
  chars: number;
  updated_at: string;
  body?: string;
}

export interface PromptList {
  items: PromptItem[];
  missing: string[];
}

/** `prompt.check` — kiểm tĩnh, chỉ cảnh báo. */
export interface PromptCheck {
  ok: boolean;
  missing: string[];
  steps_without_prompt: Record<string, string[]>;
  warnings: { key: string; code: string; message: string }[];
}

export interface PromptPreview {
  key: string;
  kind: 'compile' | 'llm';
  text?: string;
  messages?: { role: string; content: string }[];
  clip_id?: number;
  warnings?: string[];
}

const GROUP_ORDER = ['compile', 'story', 'check'] as const;

/** Nhóm hợp đồng theo thứ tự hiển thị (Ráp prompt · AI viết · Kiểm tra & khác); nhóm rỗng bị bỏ. */
export function groupContracts(items: PromptContract[]): { group: string; label: string; items: PromptContract[] }[] {
  return GROUP_ORDER.map((g) => {
    const rows = items.filter((c) => c.group === g);
    return { group: g, label: rows[0]?.group_label ?? g, items: rows };
  }).filter((g) => g.items.length > 0);
}

/** Chữ hiển thị của kết quả xem trước: prompt cuối (ráp) hoặc tin nhắn theo vai (LLM). */
export function previewText(p: PromptPreview): string {
  if (p.kind === 'llm') return (p.messages ?? []).map((m) => `[${m.role}]\n${m.content}`).join('\n\n');
  return p.text ?? '';
}
