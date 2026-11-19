import { beforeEach, describe, expect, it } from 'vitest';
import { countActive, normalizeJobEvent, summarize, useJobs } from './jobs';

/** Payload thật của `job.updated` = `job_dict` (services/jobs.py). */
function jobDict(over: Record<string, unknown> = {}) {
  return {
    id: 1,
    type: 'provider.try_image',
    status: 'running',
    priority: 0,
    lane: 'conn:1',
    project_id: null,
    sequence_id: null,
    clip_id: null,
    parent_id: null,
    params: { prompt: 'a cat' },
    progress: 0,
    stage: '',
    eta_at: null,
    external_id: null,
    error_code: null,
    error_message: null,
    hint: null,
    created_at: '2026-11-19T12:11:32.994338+00:00',
    ...over,
  };
}

const NOW = Date.parse('2026-11-19T12:00:00Z');

describe('normalizeJobEvent (payload job_dict thật)', () => {
  it('progress là phần trăm 0–100: 1 nghĩa là 1%, không phải 100%', () => {
    expect(normalizeJobEvent(jobDict({ progress: 1 }), NOW)?.progress).toBe(1);
    expect(normalizeJobEvent(jobDict({ progress: 0.5 }), NOW)?.progress).toBe(0.5);
    expect(normalizeJobEvent(jobDict({ progress: 46.4 }), NOW)?.progress).toBe(46.4);
    expect(normalizeJobEvent(jobDict({ progress: 140 }), NOW)?.progress).toBe(100);
  });

  it('eta_at (ISO UTC) → số giây còn lại; chuỗi không có múi giờ coi là UTC', () => {
    expect(normalizeJobEvent(jobDict({ eta_at: '2026-11-19T12:02:00+00:00' }), NOW)?.etaS).toBe(120);
    expect(normalizeJobEvent(jobDict({ eta_at: '2026-11-19T12:02:00' }), NOW)?.etaS).toBe(120);
    expect(normalizeJobEvent(jobDict({ eta_at: '2026-11-19T11:00:00Z' }), NOW)?.etaS).toBe(0);
    expect(normalizeJobEvent(jobDict({ eta_at: null }), NOW)?.etaS).toBeNull();
  });

  it('lô = parent_id; không có cha → không đặt lô (gộp lô chung)', () => {
    expect(normalizeJobEvent(jobDict({ parent_id: 9 }), NOW)).toMatchObject({ batch: 'p9', label: 'Lô #9' });
    expect(normalizeJobEvent(jobDict(), NOW)?.batch).toBeUndefined();
  });

  it('giữ type và stage; bỏ stage rỗng', () => {
    expect(normalizeJobEvent(jobDict({ stage: 'Đang dựng video giả' }), NOW)).toMatchObject({
      type: 'provider.try_image',
      stage: 'Đang dựng video giả',
    });
    expect(normalizeJobEvent(jobDict({ stage: '' }), NOW)?.stage).toBeUndefined();
  });

  it('dữ liệu hỏng → null', () => {
    expect(normalizeJobEvent(null)).toBeNull();
    expect(normalizeJobEvent({ status: 'running' })).toBeNull();
    expect(normalizeJobEvent('x')).toBeNull();
  });
});

describe('store job', () => {
  beforeEach(() => useJobs.getState().reset());

  it('áp sự kiện theo thứ tự: running → tiến độ → succeeded (ETA xoá khi kết thúc)', () => {
    const s = useJobs.getState();
    s.applyEvent(jobDict({ status: 'queued' }));
    s.applyEvent(jobDict({ progress: 40, stage: 'Ảnh → video', eta_at: new Date(Date.now() + 60_000).toISOString() }));
    let j = useJobs.getState().jobs[1];
    expect(j).toMatchObject({ status: 'running', progress: 40, stage: 'Ảnh → video', batch: 'all' });
    expect(j.etaS).toBeGreaterThan(50);
    s.applyEvent(jobDict({ status: 'succeeded', progress: 100, eta_at: null }));
    j = useJobs.getState().jobs[1];
    expect(j).toMatchObject({ status: 'succeeded', progress: 100, etaS: null });
  });

  it('tổng hợp lô theo parent_id; needs_attention tính là lỗi; huỷ không tính', () => {
    const s = useJobs.getState();
    s.applyEvent(jobDict({ id: 1, parent_id: 5, status: 'succeeded' }));
    s.applyEvent(jobDict({ id: 2, parent_id: 5, status: 'running' }));
    s.applyEvent(jobDict({ id: 3, parent_id: 5, status: 'needs_attention' }));
    s.applyEvent(jobDict({ id: 4, parent_id: 5, status: 'cancelled' }));
    s.applyEvent(jobDict({ id: 5, status: 'queued' }));
    const batches = summarize(useJobs.getState().jobs);
    const lot = batches.find((b) => b.key === 'p5')!;
    expect(lot).toMatchObject({ label: 'Lô #5', total: 3, done: 1, running: 1, failed: 1, active: 1 });
    expect(batches.find((b) => b.key === 'all')).toMatchObject({ total: 1, active: 1 });
    expect(countActive(useJobs.getState().jobs)).toEqual({ running: 1, queued: 1, failed: 1 });
  });
});
