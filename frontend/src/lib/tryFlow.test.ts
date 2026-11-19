import { describe, expect, it } from 'vitest';
import { ApiError } from '../api/client';
import type { TryEstimate } from '../api/types';
import { estimateRows, INITIAL, supportsDryRun, tryReducer, type TryState } from './tryFlow';

const est = (over: Partial<TryEstimate> = {}): TryEstimate => ({
  units: 1,
  cost: 0,
  kind: 'image',
  connection: 'Ảnh giả',
  connection_id: 1,
  adapter: 'fake_image',
  enabled: true,
  budget: { enabled: true, total: 60, spent: 12, remaining: 48 },
  ...over,
});

const run = (s: TryState, ...actions: Parameters<typeof tryReducer>[1][]) => actions.reduce(tryReducer, s);
const err = new ApiError({ code: 'budget_exceeded', detail: 'Vượt ngân sách ảnh.', status: 402 });

describe('supportsDryRun', () => {
  it('chỉ ảnh, video, TTS có ước tính trước (dry_run)', () => {
    expect(['image', 'video', 'tts', 'llm', 'stt'].filter((k) => supportsDryRun(k as never))).toEqual(['image', 'video', 'tts']);
  });
});

describe('tryReducer: ước tính → chạy → theo dõi job', () => {
  it('luồng đủ: gửi form → đang ước tính → bảng ước tính → chạy → job', () => {
    let s = run(INITIAL, { type: 'submit', dryRun: true });
    expect(s.step).toBe('estimating');
    s = run(s, { type: 'estimated', estimate: est() });
    expect(s).toMatchObject({ step: 'estimate', estimate: { units: 1 } });
    s = run(s, { type: 'run' });
    expect(s.step).toBe('running');
    s = run(s, { type: 'started', jobId: 42 });
    expect(s).toMatchObject({ step: 'job', jobId: 42, error: null });
  });

  it('không có dry_run (LLM, STT): gửi form → bước xác nhận không có số liệu → chạy', () => {
    const s = run(INITIAL, { type: 'submit', dryRun: false });
    expect(s).toMatchObject({ step: 'confirm', estimate: null });
    expect(run(s, { type: 'run' }).step).toBe('running');
  });

  it('ước tính lỗi → về form kèm lỗi; chạy lỗi (vd vượt ngân sách) → về bảng ước tính kèm lỗi, giữ ước tính', () => {
    let s = run(INITIAL, { type: 'submit', dryRun: true }, { type: 'failed', error: err });
    expect(s).toMatchObject({ step: 'form', error: err });
    s = run(INITIAL, { type: 'submit', dryRun: true }, { type: 'estimated', estimate: est() }, { type: 'run' }, { type: 'failed', error: err });
    expect(s).toMatchObject({ step: 'estimate', error: err, estimate: { connection_id: 1 } });
  });

  it('huỷ ở bảng ước tính → về form (sửa prompt rồi ước tính lại); reset → trạng thái đầu', () => {
    const s = run(INITIAL, { type: 'submit', dryRun: true }, { type: 'estimated', estimate: est() }, { type: 'back' });
    expect(s).toMatchObject({ step: 'form', estimate: null });
    expect(run(s, { type: 'reset' })).toEqual(INITIAL);
  });

  it('bỏ qua hành động không hợp bước (vd bấm Chạy hai lần)', () => {
    const s = run(INITIAL, { type: 'submit', dryRun: true }, { type: 'estimated', estimate: est() }, { type: 'run' });
    expect(run(s, { type: 'run' })).toBe(s);
    expect(run(INITIAL, { type: 'started', jobId: 1 })).toBe(INITIAL);
  });
});

describe('estimateRows', () => {
  it('một hàng theo loại: 1 việc × số lượt; tiền chỉ khi provider báo', () => {
    expect(estimateRows(est({ units: 2 }))).toEqual([{ kind: 'Ảnh', quantity: 1, unitCredits: 2, unitVnd: null }]);
    expect(estimateRows(est({ kind: 'video', units: undefined }))).toEqual([{ kind: 'Clip', quantity: 1, unitCredits: 1, unitVnd: null }]);
    expect(estimateRows(est({ kind: 'tts' }))[0].kind).toBe('TTS');
  });
});
