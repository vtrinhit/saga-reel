import { create } from 'zustand';

export type ToastTone = 'info' | 'success' | 'error';

export interface ToastItem {
  id: number;
  message: string;
  tone: ToastTone;
  /** Nút hành động phụ ("Thử lại"). */
  action?: { label: string; onClick: () => void };
  /** Có Hoàn tác: sống 8 giây, Ctrl Z cũng hoàn tác. */
  undo?: () => void;
  /** ms; mặc định 8000 khi có undo, 5000 khi không. */
  duration: number;
}

export const MAX_TOASTS = 3;

interface ToastState {
  toasts: ToastItem[];
  push: (t: Omit<ToastItem, 'id' | 'duration' | 'tone'> & { tone?: ToastTone; duration?: number }) => number;
  dismiss: (id: number) => void;
  clear: () => void;
}

let seq = 0;

export const useToasts = create<ToastState>((set) => ({
  toasts: [],
  push: (t) => {
    const id = ++seq;
    const item: ToastItem = { tone: 'info', duration: t.undo ? 8000 : 5000, ...t, id };
    set((s) => ({ toasts: [...s.toasts, item].slice(-MAX_TOASTS) }));
    return id;
  },
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((x) => x.id !== id) })),
  clear: () => set({ toasts: [] }),
}));

/** Gọi ngoài React: `toast({ message: 'Đã chuyển 12 clip vào Thùng rác.', undo })`. */
export const toast = (t: Parameters<ToastState['push']>[0]) => useToasts.getState().push(t);
