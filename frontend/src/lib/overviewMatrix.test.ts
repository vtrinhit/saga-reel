import { describe, expect, it } from 'vitest';
import type { SequenceInfo } from '../api/types';
import { buildMatrixRows, cellText, inGroup, rowStages, type GroupNode, type PipelineSummary } from './overviewMatrix';

const seq = (id: number, extra: Partial<SequenceInfo> = {}): SequenceInfo => ({
  id, project_id: 1, group_id: null, key: `ep${id}`, number: id, title: `Tập ${id}`, synopsis: '', script: '', status: 'draft',
  workflow_key: '', gates: {}, pipeline: {}, counts: { clips: 0 }, ...extra,
});

describe('rowStages', () => {
  it('danh sách bước của pipeline.status → 8 chặng có trạng thái thật', () => {
    const s: PipelineSummary = {
      sequence_id: 1, status: 'waiting_review', current: 'gate:storyboard', waiting: { gate: 'storyboard' }, progress: 40,
      steps: [
        { name: 'outline', enabled: true, status: 'done' },
        { name: 'write_script', enabled: true, status: 'done' },
        { name: 'split_clips', enabled: true, status: 'done' },
        { name: 'generate_images', enabled: true, status: 'done' },
        { name: 'gate:storyboard', enabled: true, status: 'waiting_review' },
        { name: 'generate_videos', enabled: false, status: 'skipped' },
      ],
      gates: {},
    };
    const st = rowStages(seq(1, { counts: { clips: 48 } }), s);
    expect(st).toHaveLength(8);
    expect(st[0].status).toBe('done');
    expect(st[2].status).toBe('wait'); // Khung hình: gate chờ bạn
    expect(st[4].status).toBe('skipped');
  });

  it('chưa chạy quy trình: dùng số clip/kịch bản', () => {
    const st = rowStages(seq(2, { script: 'x', counts: { clips: 12 } }), undefined);
    expect(st[0].status).toBe('done');
    expect(cellText(st[1])).toBe('12 clip');
    expect(cellText(st[3])).toBe('chưa tới');
    expect(cellText(rowStages(seq(3, { counts: { clips: 1000 } }), undefined)[1])).toBe('1.000 clip');
  });
});

describe('buildMatrixRows', () => {
  const groups = new Map<number, GroupNode>([
    [10, { id: 10, parent_id: null, kind: 'arc', key: 'a1', title: 'Arc 1 · Miếu hoang', rank: 'a' }],
    [11, { id: 11, parent_id: 10, kind: 'arc', key: 'a1b', title: 'Arc 1b', rank: 'b' }],
  ]);
  const list = [seq(1, { group_id: 10 }), seq(2, { group_id: 10 }), seq(3, { group_id: null })];

  it('hàng nhóm + hàng tập, thu gọn nhóm', () => {
    const rows = buildMatrixRows(list, new Map(), groups);
    expect(rows.map((r) => (r.kind === 'group' ? `G:${r.title}:${r.count}` : r.seq.id))).toEqual(['G:Arc 1 · Miếu hoang:2', 1, 2, 'G:Chưa xếp nhóm:1', 3]);
    const col = buildMatrixRows(list, new Map(), groups, new Set([10]));
    expect(col.map((r) => (r.kind === 'group' ? 'G' : r.seq.id))).toEqual(['G', 'G', 3]);
  });

  it('không có nhóm → không có hàng tiêu đề', () => {
    expect(buildMatrixRows(list, new Map(), new Map()).every((r) => r.kind === 'seq')).toBe(true);
  });

  it('inGroup theo cây cha', () => {
    expect(inGroup(seq(5, { group_id: 11 }), 10, groups)).toBe(true);
    expect(inGroup(seq(5, { group_id: null }), 10, groups)).toBe(false);
  });
});
