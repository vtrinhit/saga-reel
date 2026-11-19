import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';
import { fetchAuthStatus } from '../api/auth';
import { requireLogin } from '../api/client';
import { useSession } from '../api/session';
import { connectEvents, type EventsConnection, type EventsState } from '../lib/events';
import { useConnection } from '../stores/connection';
import { useJobs } from '../stores/jobs';
import { toast } from '../stores/toast';

const RECONNECTED_MS = 3000;

/**
 * Luồng sự kiện đứt mà không biết lý do (EventSource không cho xem mã HTTP): hỏi `/api/auth/status`; nếu phiên đã
 * mất thì đi vào cùng luồng "Phiên đã hết hạn" như request 401.
 */
async function probeAuth(): Promise<void> {
  try {
    const status = await fetchAuthStatus();
    if (status && !status.authenticated) requireLogin();
  } catch {
    // mạng/máy chủ hỏng: banner kết nối lo, không phải chuyện phiên
  }
}

/**
 * Nối `/api/events` **một lần** cho cả shell (đổi project dùng `setProject` trên cùng kết nối, giữ `boot_id`);
 * đẩy trạng thái vào store kết nối, job vào store job.
 */
export function useLiveEvents(project?: number): void {
  const queryClient = useQueryClient();
  const connRef = useRef<EventsConnection | null>(null);
  const projectRef = useRef(project);
  projectRef.current = project;

  useEffect(() => {
    const conn = useConnection.getState();
    let prev: EventsState | null = null;
    let hideTimer: ReturnType<typeof setTimeout> | null = null;

    const stop = connectEvents({
      project: projectRef.current,
      queryClient,
      onState: (s) => {
        const wasDown = prev === 'reconnecting' || prev === 'offline';
        const wasUp = prev === null || prev === 'open';
        prev = s;
        if (s === 'open') {
          conn.set({ state: s, nextRetryAt: null, justReconnected: wasDown });
          if (wasDown) {
            if (hideTimer) clearTimeout(hideTimer);
            hideTimer = setTimeout(() => useConnection.getState().set({ justReconnected: false }), RECONNECTED_MS);
          }
        } else {
          conn.set({ state: s, justReconnected: false });
          if (wasUp) void probeAuth(); // lần đứt đầu tiên: có phải do hết phiên?
        }
      },
      onRetryIn: (ms) => conn.set({ nextRetryAt: ms == null ? null : Date.now() + ms }),
      onEvent: (e) => {
        switch (e.type) {
          case 'job.updated':
            useJobs.getState().applyEvent(e.data);
            break;
          case 'resync':
            useJobs.getState().reset();
            break;
          case 'server.restarted':
            useJobs.getState().reset();
            toast({ message: 'Máy chủ vừa khởi động lại. Đã tải lại dữ liệu đang xem.' });
            break;
        }
      },
    });
    connRef.current = stop;
    conn.set({ reconnectNow: stop.reconnectNow });

    // Đăng nhập lại xong (modal "Phiên đã hết hạn") → nối lại ngay, không chờ backoff.
    const unsub = useSession.subscribe((s, before) => {
      if (s.resumedCount !== before.resumedCount) stop.reconnectNow();
    });

    return () => {
      unsub();
      stop();
      connRef.current = null;
      if (hideTimer) clearTimeout(hideTimer);
      useConnection.getState().set({ state: null, nextRetryAt: null, justReconnected: false, reconnectNow: () => {} });
    };
  }, [queryClient]);

  // Đổi project: cùng kết nối (giữ boot_id), connectEvents tự coi là khoảng trống → resync → store job đặt lại.
  useEffect(() => {
    connRef.current?.setProject(project);
  }, [project]);
}
