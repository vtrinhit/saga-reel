import { describe, expect, it } from 'vitest';
import type { Job } from '../api/types';
import {
  ERROR_TEXT,
  errorLines,
  jobActionLabel,
  jobActions,
  jobErrorLines,
  jobErrorText,
  jobStageText,
  jobStatusView,
  laneLabel,
} from './jobText';

type ActionJob = Parameters<typeof jobActions>[0];
const job = (j: Partial<Job>): ActionJob => ({
  status: 'failed',
  provider_holds: false,
  needs_decision: false,
  external_id: null,
  external_state: {},
  ...j,
});

describe('jobActions — nút thử lại/xử lý theo đúng luật backend', () => {
  it('việc kiểu openai_images đã xong (state completed, không id): chỉ tạo việc trả phí mới', () => {
    const a = jobActions(job({ status: 'succeeded', provider_holds: true, external_state: { state: 'completed', result_file: 'a.png' } }));
    expect(a).toEqual({ attention: false, retry: true, plainRetry: false, forceNew: true, lockPatch: false });
    expect(jobActionLabel(a)).toBe('Tạo việc trả phí mới');
  });

  it('việc kiểu openai_images thất bại sau khi đã có ảnh: nối lại được, hoặc tạo việc trả phí mới', () => {
    const a = jobActions(job({ status: 'failed', provider_holds: true, external_state: { state: 'completed' } }));
    expect(a).toMatchObject({ retry: true, plainRetry: true, forceNew: true, attention: false });
    expect(jobActionLabel(a)).toBe('Thử lại');
  });

  it('việc bị huỷ đúng lúc đang POST (dấu submitting, không id): chỉ "Xử lý" (clear_attention), không thử lại', () => {
    const marked = { state: 'submitting', client_ref: 'sagareel:job:7' };
    const a = jobActions(job({ status: 'cancelled', needs_decision: true, external_state: marked }));
    expect(a).toEqual({ attention: true, retry: false, plainRetry: false, forceNew: false, lockPatch: true });
    expect(jobActionLabel(a)).toBe('Xử lý');
    // không có ref đã gửi (vd. MaiVideo): gửi lại là POST mới → sửa prompt được
    expect(jobActions(job({ status: 'cancelled', needs_decision: true, external_state: { state: 'submitting' } })).lockPatch).toBe(false);
  });

  it('việc provider đã nhận (có id): thử lại = nối lại, có lựa chọn tạo việc trả phí mới', () => {
    const a = jobActions(job({ status: 'failed', provider_holds: true, external_id: 'p-1', external_state: { state: 'submitted' } }));
    expect(a).toEqual({ attention: false, retry: true, plainRetry: true, forceNew: true, lockPatch: false });
  });

  it('needs_attention: "Xử lý"; provider giữ việc (remote_cancelled) thì gửi lại sửa prompt được', () => {
    const held = jobActions(job({ status: 'needs_attention', provider_holds: true, external_id: 'p-2', external_state: { state: 'submitted' } }));
    expect(held).toMatchObject({ attention: true, retry: false, lockPatch: false });
    const marked = jobActions(job({ status: 'needs_attention', external_state: { state: 'submitting', client_ref: 'r:g1:ab' } }));
    expect(marked).toMatchObject({ attention: true, lockPatch: true });
  });

  it('việc thường thất bại / đang chạy', () => {
    expect(jobActions(job({ status: 'failed' }))).toEqual({ attention: false, retry: true, plainRetry: true, forceNew: false, lockPatch: false });
    const running = jobActions(job({ status: 'running', provider_holds: true }));
    expect(running).toMatchObject({ attention: false, retry: false, forceNew: false });
    expect(jobActionLabel(running)).toBeNull();
  });
});

describe('errorLines — câu tiếng Việt + chi tiết số liệu + gợi ý của server', () => {
  it('giữ chi tiết (số liệu) và gợi ý dưới câu đã dịch', () => {
    expect(jobErrorLines({ error_code: 'provider_rejected', error_message: 'MaiVideo từ chối ảnh (502).', hint: 'Thử lại sau.' })).toEqual({
      text: 'Provider từ chối yêu cầu.',
      detail: 'MaiVideo từ chối ảnh (502).',
      hint: 'Thử lại sau.',
    });
  });

  it('lỗi nội bộ không lộ chi tiết tiếng Anh; mã lạ dùng câu server một lần', () => {
    expect(jobErrorLines({ error_code: 'internal_error', error_message: "Lỗi nội bộ: KeyError: 'x'", hint: 'Xem nhật ký job.' })).toEqual({
      text: 'Lỗi nội bộ không lường trước.',
      detail: null,
      hint: 'Xem nhật ký job.',
    });
    expect(errorLines({ code: 'maivideo_bad_size', detail: 'Kích thước ảnh không hợp lệ.', hint: null })).toEqual({
      text: 'Kích thước ảnh không hợp lệ.',
      detail: null,
      hint: null,
    });
    expect(jobErrorLines({ error_code: null, error_message: null, hint: null })).toBeNull();
  });

  it('gợi ý vẫn hiện khi chỉ có gợi ý', () => {
    expect(jobErrorLines({ error_code: null, error_message: null, hint: 'Kiểm tra provider.' })).toEqual({ text: null, detail: null, hint: 'Kiểm tra provider.' });
  });
});

describe('jobStageText', () => {
  it('chỉ hiện công đoạn khi việc đang chạy (việc đã xong không còn công đoạn cũ)', () => {
    expect(jobStageText({ status: 'running', stage: 'Grok đang tạo video (render)' })).toBe('Grok đang tạo video (render)');
    for (const status of ['succeeded', 'failed', 'cancelled', 'interrupted', 'needs_attention', 'queued']) {
      expect(jobStageText({ status, stage: 'Đang gọi LLM' }), status).toBeNull();
    }
    expect(jobStageText({ status: 'running', stage: '' })).toBeNull();
  });
});

describe('jobText', () => {
  it('trạng thái → pill + nhãn tiếng Việt; tiến độ làm tròn xuống', () => {
    expect(jobStatusView({ status: 'running', progress: 46.9 })).toEqual({ pill: 'running', label: 'Đang chạy 46%' });
    expect(jobStatusView({ status: 'needs_attention', progress: 0 })).toEqual({ pill: 'warn', label: 'Cần xử lý' });
    expect(jobStatusView({ status: 'succeeded', progress: 100 }).label).toBe('Xong');
  });

  it('lane conn:<id> → tên kết nối', () => {
    expect(laneLabel('conn:2', [{ id: 2, name: 'GrokTele' }])).toBe('GrokTele');
    expect(laneLabel('conn:9')).toBe('Kết nối #9');
    expect(laneLabel('ffmpeg')).toBe('ffmpeg');
  });

  it('lỗi nội bộ không lộ tên exception tiếng Anh; lỗi AppError giữ câu tiếng Việt', () => {
    expect(jobErrorText({ error_code: 'internal_error', error_message: "Lỗi nội bộ không lường trước: KeyError: 'x'" })).toBe(
      'Lỗi nội bộ không lường trước.',
    );
    expect(jobErrorText({ error_code: 'provider_unavailable', error_message: 'Kết nối đang tắt: X.' })).toContain('Provider không dùng được');
    // mã chưa có trong bảng → câu của server
    expect(jobErrorText({ error_code: 'maivideo_bad_size', error_message: 'Kích thước ảnh không hợp lệ.' })).toBe('Kích thước ảnh không hợp lệ.');
    expect(jobErrorText({ error_code: 'x_y', error_message: null })).toBe('Lỗi (x_y).');
    expect(jobErrorText({ error_code: null, error_message: null })).toBeNull();
  });

  it('có câu tiếng Việt cho các mã lỗi thường gặp', () => {
    for (const code of [
      'provider_unavailable', 'provider_rejected', 'capability_mismatch', 'job_unknown',
      'maivideo_job_unknown', 'remote_cancelled', 'retries_exhausted', 'interrupted', 'already_succeeded',
      'params_patch_requires_new_job', 'params_patch_invalid',
    ]) {
      expect(ERROR_TEXT[code], code).toMatch(/[ạ-ỹà-ýăđơư]/i);
    }
  });
});
