/**
 * Nơi gắn portal cho lớp nổi (Modal, Drawer, Toast). `showModal()` đưa `<dialog>` lên top layer và làm phần còn lại
 * của trang trơ (inert): Modal portal vào `body` khi trình xem media đang mở sẽ nằm dưới và không bấm được. Vì vậy
 * portal vào `<dialog>` modal đang mở trên cùng (nếu có), không thì `body`.
 */
/** Sự kiện trên `window` khi một `<dialog>` modal mở/đóng — lớp nổi đã gắn (toast) vẽ lại vào đúng chỗ. */
export const TOP_LAYER_EVENT = 'sr-top-layer';

export function announceTopLayer(): void {
  window.dispatchEvent(new Event(TOP_LAYER_EVENT));
}

export function portalRoot(): HTMLElement {
  if (typeof document === 'undefined') return undefined as unknown as HTMLElement;
  const open = Array.from(document.querySelectorAll<HTMLDialogElement>('dialog[open]')).filter((d) => {
    try {
      return d.matches(':modal');
    } catch {
      return d.dataset.modal === 'true';
    }
  });
  return open[open.length - 1] ?? document.body;
}
