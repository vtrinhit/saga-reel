import { describe, expect, it } from 'vitest';
import { cardColumns, cardRowHeight, cardState, defaultPart, effectiveLength, totalLength } from './clipCard';
import type { Clip } from './episodeTypes';
import { shotOption, shotOptions, shotOptionText, shotText } from './shotLabels';
import { stepIndex, swipeAction, viewerKeyAction } from './viewerNav';

const clip = (extra: Partial<Clip> = {}): Clip => ({
  id: 1, sequence_id: 7, scene_id: 1, rank: 'a', key: 'c0001', title: '', image_prompt: 'Sân gạch đỏ', image_prompt_src: {},
  motion_prompt: '', motion_prompt_src: {}, prompt_mode: 'synced', assets: [], engine: 'ai_video', duration_ms: 6000,
  link: 'cut', image_take_id: null, video_take_id: null, flags: {}, review: 'pending', notes: '', shot_size: 'ms',
  angle: '', camera_move: '', lens: '', composition: '', eyeline: '', screen_dir: '', custom: {}, updated_at: '',
  ...extra,
});

describe('cardState — máy trạng thái card', () => {
  it('chưa có ảnh → empty, hành động chính "Tạo ảnh"', () => {
    const s = cardState(clip(), null);
    expect(s.kind).toBe('empty');
    expect(s.primary).toEqual({ part: 'image', label: 'Tạo ảnh' });
    expect(s.mediaId).toBeNull();
  });

  it('có ảnh, chưa có video → image, hành động chính "Tạo video"', () => {
    const s = cardState(clip({ image_take_id: 3, image_media_id: 30 }), null);
    expect(s.kind).toBe('image');
    expect(s.mediaId).toBe(30);
    expect(s.primary?.label).toBe('Tạo video');
  });

  it('có video → mặc định hiện video (poster = thumb của file video), không còn nút chính', () => {
    const c = clip({ image_take_id: 3, image_media_id: 30, video_take_id: 4, video_media_id: 40 });
    expect(defaultPart(c)).toBe('video');
    const s = cardState(c, null);
    expect(s.kind).toBe('video');
    expect(s.mediaId).toBe(40);
    expect(s.videoMediaId).toBe(40);
    expect(s.primary).toBeNull();
    // lật về ảnh trên chính card
    const img = cardState(c, 'image');
    expect(img.kind).toBe('image');
    expect(img.mediaId).toBe(30);
    expect(img.primary).toBeNull();
  });

  it('muốn xem video khi chưa có video → vẫn hiện ảnh', () => {
    expect(cardState(clip({ image_take_id: 3, image_media_id: 30 }), 'video').part).toBe('image');
  });

  it('đang có việc chạy/chờ → generating (ưu tiên hơn lỗi), kèm tiến độ', () => {
    const c = clip({ flags: { failed: { part: 'image', message: 'x' } } });
    const s = cardState(c, null, { id: 9, status: 'running', progress: 46.4, part: 'image' });
    expect(s.kind).toBe('generating');
    expect(s.view.label).toBe('Đang tạo 46%');
    expect(s.primary).toBeNull();
    expect(cardState(c, null, { id: 9, status: 'queued', progress: null, position: 3 }).view.label).toBe('Chờ #3');
    // việc đã xong không còn tính là đang tạo
    expect(cardState(c, null, { id: 9, status: 'succeeded', progress: 100 }).kind).toBe('error');
  });

  it('lỗi → error kèm lý do và "Thử lại" đúng phần lỗi', () => {
    const s = cardState(clip({ flags: { failed: { part: 'image', message: 'MaiVideo trả 502' } } }), null);
    expect(s.kind).toBe('error');
    expect(s.view.error).toBe('MaiVideo trả 502');
    expect(s.primary).toEqual({ part: 'image', label: 'Thử lại' });
    const v = cardState(
      clip({ image_take_id: 3, image_media_id: 30, video_take_id: 4, video_media_id: 40, flags: { failed_video: 'hết hạn' } }),
      null,
    );
    expect(v.kind).toBe('error');
    expect(v.primary?.part).toBe('video');
    // lỗi video không che ảnh khi đang xem ảnh
    expect(cardState(clip({ image_take_id: 3, image_media_id: 30, flags: { failed_video: 'x' } }), 'image').kind).toBe('image');
  });

  it('trạng thái phụ (cũ / lệch / đã duyệt) nằm trong view để vẽ huy hiệu', () => {
    expect(cardState(clip({ image_take_id: 3, flags: { stale_image: true } }), null).view.status).toBe('stale');
    expect(cardState(clip({ image_take_id: 3, flags: { drift: true } }), null).view.status).toBe('drifted');
    expect(cardState(clip({ image_take_id: 3, review: 'approved' }), null).view.status).toBe('approved');
  });
});

describe('độ dài hiệu lực (tự cắt)', () => {
  const at = { applied: true, in_ms: 400, out_ms: 2200, dur_ms: 3400, reason: 'speech', take_ms: 6000 };
  const vid = { image_take_id: 3, video_take_id: 4, video_media_id: 40, source_duration_ms: 6042 };
  it('tự cắt đã áp dụng → dur_ms của đề xuất, ghi "tự cắt"', () => {
    expect(effectiveLength(clip({ ...vid, auto_trim: at }))).toEqual({ ms: 3400, note: 'tự cắt' });
  });
  it('chưa phân tích / thủ công / chưa có video → duration_ms', () => {
    expect(effectiveLength(clip({ ...vid, auto_trim: { ...at, applied: false, dur_ms: null } })).ms).toBe(6000);
    expect(effectiveLength(clip({ ...vid, auto_trim: at, trim_mode: 'manual' })).note).toBe('');
    expect(effectiveLength(clip({ auto_trim: at })).ms).toBe(6000);
  });
  it('nguyên take → độ dài take; tổng tập cộng theo độ dài hiệu lực', () => {
    expect(effectiveLength(clip({ ...vid, auto_trim: at, trim_mode: 'none' }))).toEqual({ ms: 6000, note: 'nguyên take' });
    expect(totalLength([clip({ ...vid, auto_trim: at }), clip()])).toBe(9400);
  });
});

describe('lưới card — cột và chiều cao hàng', () => {
  it('2 cột ≤ 480, 3 ở ~768, 4–6 ở 1280/1920, tối đa 6', () => {
    expect(cardColumns(343, '9:16')).toBe(2);
    expect(cardColumns(644, '9:16')).toBe(3);
    expect(cardColumns(1156, '9:16')).toBe(5);
    expect(cardColumns(1660, '9:16')).toBe(6);
    expect(cardColumns(3000, '9:16')).toBe(6);
    expect(cardColumns(343, '16:9')).toBe(1);
    expect(cardColumns(1660, '16:9')).toBe(4);
  });

  it('chiều cao hàng cố định theo bề rộng, thêm một dòng khi có thoại', () => {
    const a = cardRowHeight(644, 3, '9:16', false);
    const b = cardRowHeight(644, 3, '9:16', true);
    expect(b - a).toBe(24);
    expect(a).toBe(Math.round(((644 - 24) / 3) * (16 / 9) + 44 + 12));
  });
});

describe('từ điển cỡ cảnh / shot', () => {
  it('mã nghề → nhãn tiếng Việt + mô tả, mã giữ làm chữ phụ', () => {
    const o = shotOption('shot_size', 'ews');
    expect(o.label).toBe('Toàn cảnh xa');
    expect(o.hint).toContain('bối cảnh');
    expect(o.code).toBe('EWS');
    expect(shotText('shot_size', 'cu')).toBe('Cận mặt');
    expect(shotText('shot_size', '')).toBe('');
    expect(shotOptionText(shotOption('camera_move', 'push_in'))).toBe('Đẩy vào — tiến lại gần, tăng cảm xúc');
  });

  it('mọi giá trị của meta.enums đều có nhãn (không còn mã trơ trọi)', () => {
    const meta = {
      shot: {
        shot_size: ['ews', 'ws', 'fs', 'mws', 'ms', 'mcu', 'cu', 'ecu', 'insert'],
        angle: ['eye', 'low', 'high', 'dutch', 'ots', 'pov', 'overhead'],
        camera_move: ['static', 'push_in', 'pull_out', 'pan_l', 'pan_r', 'tilt_u', 'tilt_d', 'truck_l', 'truck_r', 'track', 'arc', 'crane_up', 'crane_down', 'handheld', 'whip_pan', 'rack_focus'],
        lens: ['24mm', '35mm', '50mm', '85mm', 'anamorphic'],
        composition: ['center', 'thirds_l', 'thirds_r', 'symmetry', 'leading_lines', 'frame_in_frame', 'negative_space', 'headroom_tight'],
        eyeline: ['screen_left', 'screen_right', 'to_camera', 'down', 'up'],
        screen_dir: ['L→R', 'R→L', 'toward_cam', 'away_cam', 'none'],
      },
      cut: { axis_side: ['A', 'B'], cut_in: ['cut', 'match_action', 'match_graphic', 'j_cut', 'l_cut', 'smash', 'cutaway', 'dissolve'], cut_out: ['cut', 'dissolve'] },
    };
    for (const key of ['shot_size', 'angle', 'camera_move', 'lens', 'composition', 'eyeline', 'screen_dir', 'axis_side', 'cut_in', 'cut_out'] as const) {
      for (const o of shotOptions(key, meta)) {
        expect(o.label, `${key}:${o.value}`).not.toBe(o.value);
        expect(o.hint, `${key}:${o.value}`).not.toBe('');
      }
    }
    expect(shotOptions('cut_out', meta).map((o) => o.value)).toEqual(['cut', 'dissolve']);
  });

  it('giá trị lạ từ server giữ nguyên mã, không vỡ', () => {
    expect(shotOptions('shot_size', { shot: { shot_size: ['ms', 'drone'] } }).map((o) => o.label)).toEqual(['Trung cảnh', 'drone']);
  });
});

describe('điều hướng trình xem', () => {
  it('trước/kế kẹp trong danh sách', () => {
    expect(stepIndex(0, 'prev', 5)).toBe(0);
    expect(stepIndex(4, 'next', 5)).toBe(4);
    expect(stepIndex(2, 'next', 5)).toBe(3);
    expect(stepIndex(2, 'last', 5)).toBe(4);
    expect(stepIndex(2, 'first', 5)).toBe(0);
  });

  it('phím ←/→, J/K; bỏ qua khi đang gõ hoặc có Ctrl/Alt', () => {
    expect(viewerKeyAction({ key: 'ArrowRight' }, false)).toBe('next');
    expect(viewerKeyAction({ key: 'ArrowLeft' }, false)).toBe('prev');
    expect(viewerKeyAction({ key: 'k' }, false)).toBe('next');
    expect(viewerKeyAction({ key: 'j' }, false)).toBe('prev');
    expect(viewerKeyAction({ key: 'ArrowRight' }, true)).toBeNull();
    expect(viewerKeyAction({ key: 'ArrowLeft', altKey: true }, false)).toBeNull();
    expect(viewerKeyAction({ key: 'x' }, false)).toBeNull();
  });

  it('vuốt ngang: trái = kế, phải = trước; vuốt dọc hay ngắn thì bỏ qua', () => {
    expect(swipeAction(-80, 10)).toBe('next');
    expect(swipeAction(90, -5)).toBe('prev');
    expect(swipeAction(30, 0)).toBeNull();
    expect(swipeAction(-80, 100)).toBeNull();
  });
});
