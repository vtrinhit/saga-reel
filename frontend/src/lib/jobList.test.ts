import { describe, expect, it } from 'vitest';
import type { Job, JobLog, JobPage } from '../api/types';
import { flattenPages, matchesFilters, mergeJobUpdate, mergeLogs, toListParams } from './jobList';

const job = (id: number, over: Partial<Job> = {}): Job =>
  ({
    id,
    type: 'provider.try_image',
    status: 'queued',
    priority: 0,
    lane: 'conn:1',
    project_id: null,
    sequence_id: null,
    clip_id: null,
    parent_id: null,
    params: {},
    progress: 0,
    stage: '',
    eta_at: null,
    client_ref: null,
    external_id: null,
    external_state: {},
    attempt: 0,
    max_attempts: 3,
    next_attempt_at: null,
    error_code: null,
    error_message: null,
    hint: null,
    result: {},
    cost_estimate: {},
    cost_actual: {},
    created_at: null,
    started_at: null,
    finished_at: null,
    updated_at: null,
    ...over,
  }) as Job;

const page = (ids: number[], next: number | null = null, over: Partial<Job> = {}): JobPage => ({
  items: ids.map((i) => job(i, over)),
  next_cursor: next,
  paused: false,
});

const ids = (pages: JobPage[]) => flattenPages(pages).map((j) => j.id);

describe('toListParams', () => {
  it('bỏ lọc rỗng, thêm cursor/limit', () => {
    expect(toListParams({ status: 'failed', type: null, lane: '' }, undefined, 50)).toEqual({ status: 'failed', limit: 50 });
    expect(toListParams({}, 40, 50)).toEqual({ cursor: 40, limit: 50 });
  });
});

describe('matchesFilters', () => {
  it('khớp theo status/type/lane; lọc trống khớp mọi job', () => {
    const j = job(1, { status: 'failed', lane: 'conn:2', type: 'provider.try_video' });
    expect(matchesFilters(j, {})).toBe(true);
    expect(matchesFilters(j, { status: 'failed', lane: 'conn:2' })).toBe(true);
    expect(matchesFilters(j, { status: 'running' })).toBe(false);
    expect(matchesFilters(j, { type: 'provider.try_image' })).toBe(false);
  });
});

describe('flattenPages', () => {
  it('nối các trang, bỏ trùng theo id, giữ thứ tự id giảm dần', () => {
    expect(ids([page([10, 9, 8], 8), page([8, 7, 6])])).toEqual([10, 9, 8, 7, 6]);
  });
});

describe('mergeJobUpdate (sự kiện job.updated vào danh sách đã tải)', () => {
  it('job đã có → thay tại chỗ, không đổi thứ tự', () => {
    const pages = [page([10, 9], 9), page([8, 7])];
    const next = mergeJobUpdate(pages, job(8, { status: 'running', progress: 46 }), {});
    expect(ids(next)).toEqual([10, 9, 8, 7]);
    expect(flattenPages(next)[2]).toMatchObject({ status: 'running', progress: 46 });
    expect(pages[1].items[0].status).toBe('queued'); // không sửa dữ liệu cũ
  });

  it('job mới hơn mọi job đã tải và khớp lọc → thêm lên đầu', () => {
    const next = mergeJobUpdate([page([10, 9], 9)], job(12), {});
    expect(ids(next)).toEqual([12, 10, 9]);
  });

  it('job mới không khớp lọc → bỏ qua', () => {
    const pages = [page([10, 9], null, { status: 'failed' })];
    expect(mergeJobUpdate(pages, job(12, { status: 'queued' }), { status: 'failed' })).toBe(pages);
  });

  it('job đã có nhưng không còn khớp lọc → giữ tại chỗ (cập nhật trạng thái) để hàng không nhảy mất khi đang xem', () => {
    const pages = [page([10, 9], null, { status: 'running' })];
    const next = mergeJobUpdate(pages, job(9, { status: 'succeeded' }), { status: 'running' });
    expect(ids(next)).toEqual([10, 9]);
    expect(flattenPages(next)[1].status).toBe('succeeded');
  });

  it('job cũ chưa có trong danh sách: nằm trong khoảng đã tải → chèn đúng chỗ; nằm ngoài (còn trang sau) → bỏ qua', () => {
    const pages = [page([10, 7], 7), page([5, 3], 3)];
    expect(ids(mergeJobUpdate(pages, job(6), {}))).toEqual([10, 7, 6, 5, 3]);
    expect(mergeJobUpdate(pages, job(2), {})).toBe(pages); // còn trang sau: sẽ hiện khi tải tiếp
    const done = [page([10, 7], null)];
    expect(ids(mergeJobUpdate(done, job(2), {}))).toEqual([10, 7, 2]); // đã tải hết: thêm vào cuối
  });

  it('chưa có trang nào → không làm gì (query sẽ tự tải)', () => {
    expect(mergeJobUpdate([], job(1), {})).toEqual([]);
  });
});

describe('mergeLogs', () => {
  const log = (id: number): JobLog => ({ id, job_id: 1, level: 'info', message: `dòng ${id}`, at: null });
  it('gộp log tải về và log realtime: bỏ trùng, sắp theo id tăng dần', () => {
    expect(mergeLogs([log(1), log(2)], [log(2), log(4), log(3)]).map((l) => l.id)).toEqual([1, 2, 3, 4]);
  });
  it('không có gì mới → trả lại mảng cũ (để React không vẽ lại)', () => {
    const a = [log(1)];
    expect(mergeLogs(a, [log(1)])).toBe(a);
  });
});
