/**
 * Hoàn tác thay đổi vừa làm. Op ghi (`clip.move`, `clip.delete`, `clip.bulk_update`…) chưa trả `change_set_id` nên tìm
 * change set mới nhất cùng tên op trong `history.list` của dự án rồi `history.undo` đúng change set đó.
 */
import { callOp } from '../api/ops';
import type { ChangeSet, HistoryPage } from '../api/types';

export async function undoLatest(projectId: number, opName: string): Promise<ChangeSet | null> {
  const page = (await callOp('history.list', { project_id: projectId, limit: 20 })) as unknown as HistoryPage;
  const cs = page.items.find((x) => x.op_name === opName && !x.undone_at && !x.actor.startsWith('agent:'));
  if (!cs) return null;
  await callOp('history.undo', { change_set_id: cs.id, project_id: projectId });
  return cs;
}
