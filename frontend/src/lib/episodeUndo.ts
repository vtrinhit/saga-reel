/**
 * Hoàn tác đúng thao tác vừa làm: op ghi (`clip.create/update/bulk_update/move/delete/split/duplicate`, `take.select`,
 * `take.verdict`) trả `change_set_id` của lần ghi → `history.undo` đúng change set đó. `change_set_id` null = không có
 * gì đổi → không có gì để hoàn tác (không hiện nút Hoàn tác).
 */
import { callOp } from '../api/ops';
import { toast } from '../stores/toast';

/** `change_set_id` trong kết quả op ghi (null nếu không có / không đổi gì). */
export function changeSetOf(result: unknown): number | null {
  const v = (result as { change_set_id?: unknown } | null)?.change_set_id;
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

/** `projectId` null/NaN = để server dùng dự án của change set. */
export async function undoChangeSet(projectId: number | null, changeSetId: number): Promise<void> {
  const pid = projectId != null && Number.isFinite(projectId) ? projectId : null;
  await callOp('history.undo', { change_set_id: changeSetId, project_id: pid });
}

/**
 * Hàm Hoàn tác cho toast từ kết quả op ghi; `undefined` khi op không đổi gì (toast không hiện nút).
 * `after` chạy sau khi hoàn tác xong (thường là làm mới cache).
 */
export function undoAction(projectId: number | null, result: unknown, after?: () => unknown): (() => void) | undefined {
  const id = changeSetOf(result);
  if (id == null) return undefined;
  return () =>
    void undoChangeSet(projectId, id).then(
      () => after?.(),
      (e: unknown) => {
        const d = (e as { detail?: unknown } | null)?.detail;
        toast({ message: `Không hoàn tác được${typeof d === 'string' && d ? `: ${d}` : '.'}`, tone: 'error' });
      },
    );
}
