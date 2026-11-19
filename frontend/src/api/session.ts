import { create } from 'zustand';

/**
 * Trạng thái "Phiên đã hết hạn" (DESIGN §5.5): khi đang làm dở mà server trả 401 `auth_required`, app không
 * chuyển trang mà hiện modal đăng nhập đè lên màn hiện tại; các request bị 401 chờ trong hàng và được gửi lại sau
 * khi đăng nhập xong.
 */
interface SessionState {
  expired: boolean;
  /** Số request đang chờ gửi lại. */
  waiting: number;
  /** Tăng mỗi lần phiên được khôi phục (để luồng sự kiện nối lại). */
  resumedCount: number;
  set: (patch: Partial<Omit<SessionState, 'set'>>) => void;
}

export const useSession = create<SessionState>((set) => ({
  expired: false,
  waiting: 0,
  resumedCount: 0,
  set: (patch) => set(patch),
}));
