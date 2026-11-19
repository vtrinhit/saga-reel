import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';
import { connectEvents, type EventsState } from '../lib/events';
import { useConnection } from '../stores/connection';
import { useJobs } from '../stores/jobs';
import { toast } from '../stores/toast';

const RECONNECTED_MS = 3000;

/** Nối `/api/events` một lần cho cả shell; đẩy trạng thái vào store kết nối và job vào store job. */
export function useLiveEvents(project?: number): void {
  const queryClient = useQueryClient();
  const hideTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const conn = useConnection.getState();
    let prev: EventsState | null = null;

    const stop = connectEvents({
      project,
      queryClient,
      onState: (s) => {
        const wasDown = prev === 'reconnecting' || prev === 'offline';
        prev = s;
        if (s === 'open') {
          conn.set({ state: s, nextRetryAt: null, justReconnected: wasDown });
          if (wasDown) {
            if (hideTimer.current) clearTimeout(hideTimer.current);
            hideTimer.current = setTimeout(() => useConnection.getState().set({ justReconnected: false }), RECONNECTED_MS);
          }
        } else {
          conn.set({ state: s, justReconnected: false });
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
    conn.set({ reconnectNow: stop.reconnectNow });

    return () => {
      stop();
      if (hideTimer.current) clearTimeout(hideTimer.current);
      useConnection.getState().set({ state: null, nextRetryAt: null, justReconnected: false, reconnectNow: () => {} });
    };
  }, [project, queryClient]);
}
