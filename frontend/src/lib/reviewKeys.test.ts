import { describe, expect, it } from 'vitest';
import { boardKeyAction, reviewKeyAction } from './reviewKeys';

const free = { typing: false, modal: false };

describe('reviewKeyAction', () => {
  it('1–9 chọn take (theo code hoặc key), Shift+số bỏ qua', () => {
    expect(reviewKeyAction({ key: '3', code: 'Digit3' }, free)).toEqual({ type: 'pick', n: 3 });
    expect(reviewKeyAction({ key: '&', code: 'Digit1' }, free)).toEqual({ type: 'pick', n: 1 });
    expect(reviewKeyAction({ key: '9', code: 'Numpad9' }, free)).toEqual({ type: 'pick', n: 9 });
    expect(reviewKeyAction({ key: '0', code: 'Digit0' }, free)).toBeNull();
    expect(reviewKeyAction({ key: '!', code: 'Digit1', shiftKey: true }, free)).toBeNull();
  });

  it('chữ đơn: A R S E V C F, không phân biệt hoa thường', () => {
    expect(reviewKeyAction({ key: 'a' }, free)).toEqual({ type: 'approve' });
    expect(reviewKeyAction({ key: 'r' }, free)).toEqual({ type: 'regenerate' });
    expect(reviewKeyAction({ key: 'R', shiftKey: true }, free)).toEqual({ type: 'editRegenerate' });
    expect(reviewKeyAction({ key: 's' }, free)).toEqual({ type: 'skip' });
    expect(reviewKeyAction({ key: 'e' }, free)).toEqual({ type: 'edit' });
    expect(reviewKeyAction({ key: 'v' }, free)).toEqual({ type: 'verdict' });
    expect(reviewKeyAction({ key: 'c' }, free)).toEqual({ type: 'compare' });
    expect(reviewKeyAction({ key: 'f' }, free)).toEqual({ type: 'drift' });
    expect(reviewKeyAction({ key: 'x' }, free)).toBeNull();
  });

  it('J/K clip trước/kế, ←/→ là bí danh; Shift J/K = clip lỗi/cũ', () => {
    expect(reviewKeyAction({ key: 'j' }, free)).toEqual({ type: 'prevClip' });
    expect(reviewKeyAction({ key: 'k' }, free)).toEqual({ type: 'nextClip' });
    expect(reviewKeyAction({ key: 'ArrowLeft' }, free)).toEqual({ type: 'prevClip' });
    expect(reviewKeyAction({ key: 'ArrowRight' }, free)).toEqual({ type: 'nextClip' });
    expect(reviewKeyAction({ key: 'J', shiftKey: true }, free)).toEqual({ type: 'prevFlagged' });
    expect(reviewKeyAction({ key: 'K', shiftKey: true }, free)).toEqual({ type: 'nextFlagged' });
  });

  it('↑/↓ đổi take, Space phát, Esc thoát, Ctrl Z hoàn tác', () => {
    expect(reviewKeyAction({ key: 'ArrowUp' }, free)).toEqual({ type: 'prevTake' });
    expect(reviewKeyAction({ key: 'ArrowDown' }, free)).toEqual({ type: 'nextTake' });
    expect(reviewKeyAction({ key: ' ' }, free)).toEqual({ type: 'play' });
    expect(reviewKeyAction({ key: 'Escape' }, free)).toEqual({ type: 'exit' });
    expect(reviewKeyAction({ key: 'z', ctrlKey: true }, free)).toEqual({ type: 'undo' });
    expect(reviewKeyAction({ key: 'z', metaKey: true }, free)).toEqual({ type: 'undo' });
  });

  it('không giành phím trình duyệt / tổ hợp lạ', () => {
    expect(reviewKeyAction({ key: 'a', ctrlKey: true }, free)).toBeNull();
    expect(reviewKeyAction({ key: 'k', ctrlKey: true }, free)).toBeNull();
    expect(reviewKeyAction({ key: 'ArrowLeft', altKey: true }, free)).toBeNull();
    expect(reviewKeyAction({ key: 'z', ctrlKey: true, shiftKey: true }, free)).toBeNull();
  });

  it('bỏ qua khi đang gõ hoặc có dialog mở', () => {
    const typing = { typing: true, modal: false };
    expect(reviewKeyAction({ key: 'a' }, typing)).toBeNull();
    expect(reviewKeyAction({ key: '1', code: 'Digit1' }, typing)).toBeNull();
    expect(reviewKeyAction({ key: 'z', ctrlKey: true }, typing)).toBeNull();
    expect(reviewKeyAction({ key: 'Escape' }, typing)).toBeNull();
    expect(reviewKeyAction({ key: 'a' }, { typing: false, modal: true })).toBeNull();
    expect(reviewKeyAction({ key: 'z', ctrlKey: true }, { typing: false, modal: true })).toBeNull();
  });
});

describe('boardKeyAction', () => {
  it('↑/↓ di chuyển, Shift mở rộng, Alt dời clip', () => {
    expect(boardKeyAction({ key: 'ArrowDown' }, free)).toEqual({ type: 'move', dir: 1, extend: false });
    expect(boardKeyAction({ key: 'ArrowUp', shiftKey: true }, free)).toEqual({ type: 'move', dir: -1, extend: true });
    expect(boardKeyAction({ key: 'ArrowUp', altKey: true }, free)).toEqual({ type: 'reorder', dir: -1 });
    expect(boardKeyAction({ key: 'ArrowDown', altKey: true }, free)).toEqual({ type: 'reorder', dir: 1 });
  });
  it('Enter mở, Ctrl Enter tạo lại, Space chọn, F2 sửa, Ctrl A chọn hết, Esc bỏ chọn', () => {
    expect(boardKeyAction({ key: 'Enter' }, free)).toEqual({ type: 'open' });
    expect(boardKeyAction({ key: 'Enter', ctrlKey: true }, free)).toEqual({ type: 'regenerate' });
    expect(boardKeyAction({ key: ' ' }, free)).toEqual({ type: 'toggle' });
    expect(boardKeyAction({ key: 'F2' }, free)).toEqual({ type: 'edit' });
    expect(boardKeyAction({ key: 'a', ctrlKey: true }, free)).toEqual({ type: 'selectAll' });
    expect(boardKeyAction({ key: 'Escape' }, free)).toEqual({ type: 'clear' });
  });
  it('không chạy khi đang gõ', () => {
    expect(boardKeyAction({ key: 'ArrowDown' }, { typing: true, modal: false })).toBeNull();
    expect(boardKeyAction({ key: ' ' }, { typing: false, modal: true })).toBeNull();
  });
});
