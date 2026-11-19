import { describe, expect, it } from 'vitest';
import { ERROR_TEXT, jobErrorText, jobStatusView, laneLabel } from './jobText';

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
    expect(jobErrorText({ error_code: 'budget_exceeded', error_message: 'Vượt ngân sách ảnh (60/60).' })).toBe('Vượt ngân sách.');
    expect(jobErrorText({ error_code: 'provider_unavailable', error_message: 'Kết nối đang tắt: X.' })).toContain('Provider không dùng được');
    // mã chưa có trong bảng → câu của server
    expect(jobErrorText({ error_code: 'maivideo_bad_size', error_message: 'Kích thước ảnh không hợp lệ.' })).toBe('Kích thước ảnh không hợp lệ.');
    expect(jobErrorText({ error_code: 'x_y', error_message: null })).toBe('Lỗi (x_y).');
    expect(jobErrorText({ error_code: null, error_message: null })).toBeNull();
  });

  it('có câu tiếng Việt cho các mã lỗi thường gặp', () => {
    for (const code of [
      'provider_unavailable', 'provider_rejected', 'budget_exceeded', 'capability_mismatch', 'job_unknown',
      'maivideo_job_unknown', 'remote_cancelled', 'retries_exhausted', 'interrupted', 'already_succeeded',
      'params_patch_requires_new_job',
    ]) {
      expect(ERROR_TEXT[code], code).toMatch(/[ạ-ỹà-ýăđơư]/i);
    }
  });
});
