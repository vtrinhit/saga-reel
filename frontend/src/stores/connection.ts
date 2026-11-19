import { create } from 'zustand';
import type { EventsState } from '../lib/events';

interface ConnectionState {
  /** null = chưa từng mở hoặc chưa có lỗi. */
  state: EventsState | null;
  /** Mốc (ms epoch) lần tự thử kế tiếp. */
  nextRetryAt: number | null;
  /** Vừa nối lại sau khi rớt: hiện "Đã kết nối lại" 3 giây. */
  justReconnected: boolean;
  reconnectNow: () => void;
  set: (patch: Partial<Omit<ConnectionState, 'set'>>) => void;
}

export const useConnection = create<ConnectionState>((set) => ({
  state: null,
  nextRetryAt: null,
  justReconnected: false,
  reconnectNow: () => {},
  set: (patch) => set(patch),
}));
