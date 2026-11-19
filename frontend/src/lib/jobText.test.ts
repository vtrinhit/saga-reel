import { describe, expect, it } from 'vitest';
import { jobErrorText, jobStatusView, laneLabel } from './jobText';

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
    expect(jobErrorText({ error_code: 'budget_exceeded', error_message: 'Vượt ngân sách ảnh.' })).toBe('Vượt ngân sách ảnh.');
    expect(jobErrorText({ error_code: 'x_y', error_message: null })).toBe('Lỗi (x_y).');
    expect(jobErrorText({ error_code: null, error_message: null })).toBeNull();
  });
});
