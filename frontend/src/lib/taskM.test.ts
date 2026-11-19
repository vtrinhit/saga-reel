import { describe, expect, it } from 'vitest';
import type { ChangeSet, Job } from '../api/types';
import { agentRows, buildNeeds, gateLabel, gateTitle, nextCost, nextSegment, type PipelineSummary } from './activity';
import { actorsOf, filterHistory, groupByDay, latestUndoable, parseConflict, versionBefore } from './historyView';
import { formatField, fromYaml, getPath, parseField, setPath, toYaml, validPresetKey } from './presets';
import { DeltaBuffer, asDelta, bibleMeta, lastSentence, sortBible, storyTaskLabel, type BibleItem } from './story';

const step = (name: string, status = 'pending', enabled = true) => ({ name, label: name, status, enabled });

const summary = (over: Partial<PipelineSummary> = {}): PipelineSummary => ({
  sequence_id: 12,
  number: 12,
  title: 'Đêm trăng máu',
  workflow: 'storyboard_first',
  status: 'waiting_review',
  job_id: 1,
  current: 'gate:animatic',
  waiting: { gate: 'animatic', since: '2026-11-20T10:00:00+00:00' },
  error: null,
  steps: [step('render_animatic', 'done'), step('gate:animatic', 'running'), step('gate:pilot', 'pending', false), step('generate_videos'), step('render'), step('gate:final'), step('export')],
  gates: {},
  progress: 50,
  updated_at: '2026-11-20T10:00:00+00:00',
  ...over,
});

const job = (over: Partial<Job>): Job =>
  ({ id: 1, type: 'generate.image', status: 'queued', project_id: 3, sequence_id: 12, params: {}, error_code: null, error_message: null, hint: null, updated_at: '2026-11-20T10:00:00Z', created_at: '2026-11-20T10:00:00Z', ...over }) as Job;

describe('activity', () => {
  it('nhãn và tiêu đề gate', () => {
    expect(gateLabel('gate:final')).toBe('bản cuối');
    expect(gateLabel('custom_check')).toBe('custom check');
    expect(gateTitle('animatic', { number: 12, title: 'Đêm trăng máu' })).toBe('Duyệt animatic · Tập 12 · Đêm trăng máu');
    expect(gateTitle('outline', { number: 3, title: 'Tập 3' })).toBe('Duyệt dàn ý · Tập 3');
  });

  it('đoạn kế sau gate dừng ở gate bật kế tiếp, bỏ gate tắt', () => {
    const seg = nextSegment(summary().steps, 'animatic');
    expect(seg.steps).toEqual(['generate_videos', 'render']);
    expect(seg.nextGate).toBe('gate:final');
    expect(seg.stage?.label).toBe('Video');
    expect(seg.spends.videos).toBe(true);
    expect(seg.spends.images).toBe(false);
  });

  it('giá bước kế: ngân sách tắt = không giới hạn; vượt ngân sách', () => {
    const seg = nextSegment(summary().steps, 'animatic');
    const plan = { sequence_id: 12, clips: 48, images: 0, videos: 48, llm_calls: 0, status: 'waiting_review' };
    const free = nextCost(seg, plan, { image: { enabled: true, total: 100, spent: 0, remaining: 100 }, video: { enabled: false, total: null, spent: 0, remaining: null } });
    expect(free.amount).toBe('48 video · 0 lượt');
    expect(free.notes).toContain('video không giới hạn ngân sách');
    const over = nextCost(seg, plan, { image: { enabled: true, total: 1, spent: 0, remaining: 1 }, video: { enabled: true, total: 10, spent: 0, remaining: 10 } });
    expect(over.overBudget).toBe(true);
    expect(over.credits).toBe(48);
    expect(nextCost(seg, null, null).amount).toBeNull();
  });

  it('gom Cần bạn: gate trước, rồi chờ nhập, lỗi, việc; gộp việc vượt ngân sách', () => {
    const needs = buildNeeds(
      [summary(), summary({ sequence_id: 13, number: 13, status: 'waiting_input', waiting: { step: 'write_script' }, current: 'write_script' }), summary({ sequence_id: 14, number: 14, status: 'failed', waiting: null, error: { message: 'LLM lỗi' } }), summary({ sequence_id: 15, status: 'running', waiting: null })],
      [job({ id: 5, status: 'failed', error_message: 'MaiVideo 502' }), job({ id: 6, status: 'needs_attention' }), job({ id: 7, error_code: 'budget_exceeded' }), job({ id: 8, error_code: 'budget_exceeded' }), job({ id: 9, status: 'running' })],
      { projectId: 3 },
    );
    expect(needs.map((n) => n.kind)).toEqual(['gate', 'input', 'pipeline_failed', 'job_attention', 'job_failed', 'budget']);
    expect(needs[0].gate).toBe('animatic');
    expect(needs[1].href).toBe('/p/3/story?seq=13');
    expect(needs.at(-1)!.title).toBe('2 việc chờ vì vượt ngân sách tháng');
  });

  it('agent: theo change set + việc đang chạy mang params.actor', () => {
    const now = Date.parse('2026-11-20T10:05:00Z');
    const rows = agentRows(
      [
        { actor: 'agent:biên-tập', created_at: '2026-11-20T10:04:00Z', summary: 'sửa prompt 12 clip', op_name: 'clip.bulk_update' },
        { actor: 'agent:biên-tập', created_at: '2026-11-20T09:00:00Z', summary: 'cũ', op_name: 'x' },
        { actor: 'user:owner', created_at: '2026-11-20T10:04:00Z', summary: 'tôi', op_name: 'x' },
        { actor: 'agent:kiểm-định', created_at: '2026-11-18T10:00:00Z', summary: 'quá cũ', op_name: 'x' },
      ],
      [job({ status: 'running', params: { actor: 'agent:kiểm-định' } })],
      now,
    );
    expect(rows.map((r) => r.name)).toEqual(['biên-tập', 'kiểm-định']);
    expect(rows[0]).toMatchObject({ changes: 2, live: true, lastSummary: 'sửa prompt 12 clip' });
    expect(rows[1]).toMatchObject({ runningJobs: 1, live: true, changes: 0 });
  });
});

const cs = (id: number, actor: string, created_at: string, over: Partial<ChangeSet> = {}): ChangeSet => ({
  id, actor, created_at, op_name: 'clip.update', summary: `cs ${id}`, undone_at: null, entities: [{ type: 'clip', id: 1, revision_id: id * 10 }], ...over,
});

describe('historyView', () => {
  it('đọc xung đột hoàn tác', () => {
    expect(parseConflict({ code: 'undo_conflict', extra: { entities: [{ type: 'clip', id: 3, change_set_id: 9 }, { bad: 1 }] } })).toEqual([{ type: 'clip', id: 3, change_set_id: 9 }]);
    expect(parseConflict({ code: 'stale_write' })).toBeNull();
  });

  it('lọc, actor, hoàn tác mới nhất, phiên bản trước', () => {
    const items = [cs(5, 'agent:a', '2026-11-20T10:00:00Z'), cs(4, 'user:owner', '2026-11-20T09:00:00Z', { undone_at: 'x' }), cs(3, 'agent:a', '2026-11-19T09:00:00Z', { op_name: 'history.undo' }), cs(2, 'user:owner', '2026-11-19T08:00:00Z')];
    expect(actorsOf(items)).toEqual(['user:owner', 'agent:a']);
    expect(filterHistory(items, 'agent:a', null).map((c) => c.id)).toEqual([5, 3]);
    expect(filterHistory(items, null, 'look')).toEqual([]);
    expect(latestUndoable(items, 'user:owner')?.id).toBe(2);
    expect(versionBefore(items, 4)?.id).toBe(3);
    expect(versionBefore(items, 2)).toBeNull();
  });

  it('gom theo ngày giờ Việt Nam', () => {
    const now = Date.parse('2026-11-20T12:00:00Z');
    const g = groupByDay([cs(2, 'x', '2026-11-20T10:00:00Z'), cs(1, 'x', '2026-11-19T10:00:00Z'), cs(0, 'x', '2026-11-10T10:00:00Z')], now);
    expect(g.map((x) => x.label.slice(0, 7))).toEqual(['Hôm nay', 'Hôm qua', g[2].label.slice(0, 7)]);
    expect(g).toHaveLength(3);
  });
});

describe('presets', () => {
  it('đường dẫn, trường form, YAML khứ hồi', () => {
    const d = { duration_s: { min: 3, max: 60 }, label: 'TikTok' };
    expect(getPath(d, ['duration_s', 'max'])).toBe(60);
    expect(setPath(d, ['duration_s', 'recommended'], [15, 30])).toEqual({ duration_s: { min: 3, max: 60, recommended: [15, 30] }, label: 'TikTok' });
    expect(setPath(d, ['label'], undefined)).toEqual({ duration_s: { min: 3, max: 60 } });
    expect(parseField('range', '60–90')).toEqual([60, 90]);
    expect(parseField('float', '4,5')).toBe(4.5);
    expect(parseField('list', 'tiktok, reels')).toEqual(['tiktok', 'reels']);
    expect(parseField('int', '')).toBeUndefined();
    expect(formatField('range', [60, 90])).toBe('60–90');
    const back = fromYaml(toYaml({ a: 1, b: ['x', 'y'], c: { d: 'Thể loại' } }));
    expect(back).toEqual({ ok: true, data: { a: 1, b: ['x', 'y'], c: { d: 'Thể loại' } } });
  });

  it('YAML lỗi có số dòng; mảng ở gốc bị từ chối', () => {
    const bad = fromYaml('a: 1\nb: [1, 2\n');
    expect(bad.ok).toBe(false);
    expect(fromYaml('- 1\n- 2')).toMatchObject({ ok: false });
    expect(validPresetKey('tiktok-cau-chuyen')).toBe(true);
    expect(validPresetKey('TikTok')).toBe(false);
  });
});

describe('story', () => {
  it('gộp chữ chạy dần theo job', () => {
    const b = new DeltaBuffer(10);
    b.push({ job_id: 1, text: 'Cảnh 1. ' });
    expect(b.push({ job_id: 1, text: 'Mưa đêm.' })).toBe('. Mưa đêm.');
    expect(b.get(2)).toBe('');
    expect(asDelta({ job_id: 3, text: 'x', task: 'story.write_script' })).toEqual({ job_id: 3, text: 'x', task: 'story.write_script' });
    expect(asDelta({ text: 'x' })).toBeNull();
  });

  it('câu trọn vẹn cuối, nhãn việc, meta và thứ tự bible', () => {
    expect(lastSentence('Đèn lồng chao. Khang dừng lại. Bà chủ')).toBe('Khang dừng lại.');
    expect(lastSentence('chưa xong')).toBe('');
    expect(storyTaskLabel('write_script')).toBe('Viết kịch bản');
    expect(bibleMeta({ type: 'fact', story_order: 3 })).toBe('sự thật · từ tập 3');
    const it0 = (id: number, type: BibleItem['type'], status: BibleItem['status']) => ({ id, type, status, story_order: null }) as BibleItem;
    expect(sortBible([it0(1, 'fact', 'confirmed'), it0(2, 'character', 'confirmed'), it0(3, 'fact', 'pending')]).map((x) => x.id)).toEqual([3, 2, 1]);
  });
});
