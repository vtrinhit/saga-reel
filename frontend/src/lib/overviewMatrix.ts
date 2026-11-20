/**
 * Ma trận Tổng quan (tập × chặng) — hàm thuần. `pipeline.status(project_id)` trả mỗi tập `{status, waiting, steps:
 * [{name, enabled, status}], gates}`; `lib/pipeline.ts::toStages` đọc thẳng dạng này.
 */
import type { SequenceInfo } from '../api/types';
import { toStages, type ClipCounts, type Stage } from './pipeline';

export interface PipelineSummary {
  sequence_id: number;
  status: string;
  current: string | null;
  waiting: unknown;
  steps: { name: string; enabled: boolean; status: string; label?: string }[];
  gates: Record<string, unknown>;
  progress: number;
}

export interface GroupNode {
  id: number;
  parent_id: number | null;
  kind: string;
  key: string;
  title: string;
  rank: string;
}

/**
 * Chặng của một hàng: bản tóm tắt `pipeline.status` (danh sách bước của lần chạy) → `toStages`; tập chưa chạy dùng
 * `workflow` (preset của tập) + số clip/kịch bản.
 */
export function rowStages(seq: SequenceInfo, summary: PipelineSummary | undefined, workflow: unknown = null): Stage[] {
  const counts: ClipCounts = { clips: Number(seq.counts?.clips ?? 0), hasScript: !!seq.script?.trim() };
  return toStages(workflow, summary ?? seq.pipeline, summary?.gates ?? seq.gates, counts);
}

export type MatrixRow =
  | { kind: 'group'; key: string; groupId: number | null; title: string; count: number }
  | { kind: 'seq'; key: string; seq: SequenceInfo; stages: Stage[] };

/**
 * Hàng ma trận: gom theo nhóm (arc) — hàng tiêu đề nhóm mỗi khi `group_id` đổi; tập không thuộc nhóm nằm dưới "Chưa
 * xếp nhóm" (chỉ hiện tiêu đề khi dự án có nhóm). `collapsed` = id nhóm đang thu gọn.
 */
export function buildMatrixRows(
  seqs: SequenceInfo[],
  summaries: Map<number, PipelineSummary>,
  groups: Map<number, GroupNode>,
  collapsed: Set<number | null> = new Set(),
  workflowOf: (seq: SequenceInfo) => unknown = () => null,
): MatrixRow[] {
  const out: MatrixRow[] = [];
  const useGroups = groups.size > 0;
  let i = 0;
  while (i < seqs.length) {
    const gid = seqs[i].group_id ?? null;
    let j = i;
    while (j < seqs.length && (seqs[j].group_id ?? null) === gid) j += 1;
    if (useGroups) {
      const g = gid != null ? groups.get(gid) : undefined;
      out.push({ kind: 'group', key: `g${i}:${gid ?? 'none'}`, groupId: gid, title: g ? g.title : 'Chưa xếp nhóm', count: j - i });
    }
    if (!collapsed.has(gid)) {
      for (let k = i; k < j; k++) out.push({ kind: 'seq', key: `s${seqs[k].id}`, seq: seqs[k], stages: rowStages(seqs[k], summaries.get(seqs[k].id), workflowOf(seqs[k])) });
    }
    i = j;
  }
  return out;
}

/** Tập thuộc arc (nhóm hoặc nhóm con của nó). */
export function inGroup(seq: SequenceInfo, groupId: number, groups: Map<number, GroupNode>): boolean {
  let g = seq.group_id != null ? groups.get(seq.group_id) : undefined;
  for (let guard = 0; g && guard < 10; guard++) {
    if (g.id === groupId) return true;
    g = g.parent_id != null ? groups.get(g.parent_id) : undefined;
  }
  return false;
}

/** Nhãn ngắn của ô cho điện thoại / sr-only. */
export function cellText(s: Stage): string {
  return raw(s).replace(/\d{4,}/g, (d) => Number(d).toLocaleString('vi-VN'));
}

function raw(s: Stage): string {
  switch (s.status) {
    case 'done':
      return s.countLabel ?? 'xong';
    case 'running':
      return s.countLabel ?? (s.progress != null ? `${s.progress}%` : 'đang chạy');
    case 'wait':
      return 'chờ bạn';
    case 'failed':
      return s.countLabel ?? 'lỗi';
    case 'stale':
      return s.countLabel ?? 'đã cũ';
    case 'skipped':
      return 'bỏ qua';
    default:
      return 'chưa tới';
  }
}
