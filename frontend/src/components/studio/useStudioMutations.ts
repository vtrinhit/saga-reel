/**
 * Ghi của Studio/Âm thanh: lạc quan cho sửa nhỏ (trim, đổi thứ tự, sửa chữ — DESIGN §7.3), lỗi thì trả về bản cũ +
 * toast "Thử lại"; mỗi lần ghi kèm `expected_updated_at` (agent ghi song song) và có Hoàn tác (`history.undo`) — đúng
 * change set của thao tác khi op trả `change_set_id` (clip.*), không thì thao tác gần nhất của bạn.
 */
import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useRef } from 'react';
import { callOp, type OpParams } from '../../api/ops';
import { toast } from '../../stores/toast';
import { sk, toastError, undoLast } from '../../lib/studioData';
import { reorder, type ClipRow, type DialogueRow, type TimelineItemRow } from '../../lib/timeline';

export function useStudioMutations(projectId: number | null, seqId: number | null) {
  const qc = useQueryClient();
  const sid = seqId ?? -1;

  // Ghi nối tiếp theo thực thể: lần ghi sau chờ lần trước xong để gửi đúng `updated_at` mới nhất (trim liên tục
  // bằng phím không tự gây stale_write với chính mình).
  const queues = useRef(new Map<string, Promise<unknown>>());
  const serial = useCallback(<T,>(k: string, fn: () => Promise<T>): Promise<T> => {
    const prev = queues.current.get(k) ?? Promise.resolve();
    const p = prev.catch(() => undefined).then(fn);
    queues.current.set(k, p);
    return p;
  }, []);

  const refresh = useCallback(() => {
    void qc.invalidateQueries({ predicate: (q) => q.queryKey[0] === 'studio' && q.queryKey[1] !== 'check' });
  }, [qc]);

  const undoToast = useCallback(
    (message: string, changeSetId?: number | null) => {
      toast({
        message,
        tone: 'success',
        undo: projectId != null ? () => void undoLast(projectId, changeSetId).then(refresh) : undefined,
      });
    },
    [projectId, refresh],
  );

  const updateClip = useCallback(
    async (clip: ClipRow, values: Partial<ClipRow> & Record<string, unknown>, label: string, opts: { quiet?: boolean } = {}) => {
      const key = sk.clips(sid);
      const prev = qc.getQueryData<ClipRow[]>(key);
      qc.setQueryData<ClipRow[]>(key, (list) => list?.map((c) => (c.id === clip.id ? { ...c, ...values } : c)));
      try {
        const next = await serial(`clip:${clip.id}`, () =>
          callOp('clip.update', {
            id: clip.id,
            values,
            expected_updated_at: qc.getQueryData<ClipRow[]>(key)?.find((c) => c.id === clip.id)?.updated_at ?? clip.updated_at ?? null,
          }) as unknown as Promise<ClipRow & { change_set_id?: number | null }>,
        );
        qc.setQueryData<ClipRow[]>(key, (list) => list?.map((c) => (c.id === clip.id ? { ...c, ...next } : c)));
        if (!opts.quiet) undoToast(`Đã lưu ${label} của ${clip.key}.`, next.change_set_id);
      } catch (e) {
        qc.setQueryData(key, prev);
        toastError(`Không lưu được ${label} của ${clip.key}`, e, () => void updateClip(clip, values, label, opts));
        refresh();
      }
    },
    [qc, sid, undoToast, refresh, serial],
  );

  const moveClip = useCallback(
    async (clip: ClipRow, to: number, after: { after_clip_id: number } | null) => {
      if (!after) return;
      const key = sk.clips(sid);
      const prev = qc.getQueryData<ClipRow[]>(key);
      if (prev) {
        const order = reorder(prev.map((c) => c.id), clip.id, to);
        const byId = new Map(prev.map((c) => [c.id, c]));
        qc.setQueryData<ClipRow[]>(key, order.map((id) => byId.get(id)!));
      }
      try {
        const r = (await callOp('clip.move', { ids: [clip.id], after_clip_id: after.after_clip_id })) as unknown as {
          change_set_id?: number | null;
        };
        undoToast(`Đã dời ${clip.key}.`, r?.change_set_id);
        refresh();
      } catch (e) {
        qc.setQueryData(key, prev);
        toastError(`Không dời được ${clip.key}`, e, () => void moveClip(clip, to, after));
      }
    },
    [qc, sid, undoToast, refresh, serial],
  );

  const updateLine = useCallback(
    async (line: DialogueRow, values: Partial<DialogueRow>, label = 'câu thoại') => {
      const key = sk.lines(sid);
      const prev = qc.getQueryData<DialogueRow[]>(key);
      qc.setQueryData<DialogueRow[]>(key, (list) => list?.map((l) => (l.id === line.id ? { ...l, ...values } : l)));
      try {
        const next = await serial(`line:${line.id}`, () =>
          callOp('dialogue.update', {
            id: line.id,
            values,
            expected_updated_at: qc.getQueryData<DialogueRow[]>(key)?.find((l) => l.id === line.id)?.updated_at ?? line.updated_at ?? null,
          }) as unknown as Promise<DialogueRow>,
        );
        qc.setQueryData<DialogueRow[]>(key, (list) => list?.map((l) => (l.id === line.id ? { ...l, ...next } : l)));
        undoToast(`Đã lưu ${label}.`);
      } catch (e) {
        qc.setQueryData(key, prev);
        toastError(`Không lưu được ${label}`, e, () => void updateLine(line, values, label));
        refresh();
      }
    },
    [qc, sid, undoToast, refresh, serial],
  );

  const updateItem = useCallback(
    async (item: TimelineItemRow, values: Partial<TimelineItemRow>, label = 'mục timeline') => {
      const key = sk.items(sid);
      const prev = qc.getQueryData<TimelineItemRow[]>(key);
      qc.setQueryData<TimelineItemRow[]>(key, (list) => list?.map((i) => (i.id === item.id ? { ...i, ...values } : i)));
      try {
        const next = await serial(`item:${item.id}`, () =>
          callOp('timeline.update', {
            id: item.id,
            values,
            expected_updated_at: qc.getQueryData<TimelineItemRow[]>(key)?.find((i) => i.id === item.id)?.updated_at ?? item.updated_at ?? null,
          }) as unknown as Promise<TimelineItemRow>,
        );
        qc.setQueryData<TimelineItemRow[]>(key, (list) => list?.map((i) => (i.id === item.id ? { ...i, ...next } : i)));
        undoToast(`Đã lưu ${label}.`);
      } catch (e) {
        qc.setQueryData(key, prev);
        toastError(`Không lưu được ${label}`, e, () => void updateItem(item, values, label));
        refresh();
      }
    },
    [qc, sid, undoToast, refresh, serial],
  );

  const addItem = useCallback(
    async (values: Omit<OpParams<'timeline.add'>, 'sequence_id'>, label: string) => {
      try {
        await callOp('timeline.add', { sequence_id: seqId!, ...values });
        undoToast(`Đã thêm ${label}.`);
        refresh();
      } catch (e) {
        toastError(`Không thêm được ${label}`, e);
      }
    },
    [seqId, undoToast, refresh],
  );

  /** Gỡ mục timeline (xoá mềm): preview lấy confirm_token rồi gỡ ngay, toast Hoàn tác (DESIGN §5.5 bước 1). */
  const removeItem = useCallback(
    async (item: TimelineItemRow, label: string) => {
      const key = sk.items(sid);
      const prev = qc.getQueryData<TimelineItemRow[]>(key);
      qc.setQueryData<TimelineItemRow[]>(key, (list) => list?.filter((i) => i.id !== item.id));
      try {
        const pv = (await callOp('timeline.remove_preview', { ids: [item.id] })) as unknown as { confirm_token: string };
        await callOp('timeline.remove', { confirm_token: pv.confirm_token });
        undoToast(`Đã xoá ${label}.`);
      } catch (e) {
        qc.setQueryData(key, prev);
        toastError(`Không xoá được ${label}`, e);
      }
    },
    [qc, sid, undoToast],
  );

  return { updateClip, moveClip, updateLine, updateItem, addItem, removeItem, refresh };
}
