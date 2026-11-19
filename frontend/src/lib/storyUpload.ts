/**
 * Upload chia khối cho video mẫu (Kênh mẫu, Clone): `upload.start` → `PUT /api/upload/{id}?offset=` từng khối →
 * `upload.finish(sha256)`. sha256 cần `crypto.subtle` (chỉ có ở ngữ cảnh an toàn: localhost hoặc https) và đọc cả file
 * vào bộ nhớ — giới hạn 500 MB ở UI.
 */
import { api } from '../api/client';
import { callOp } from '../api/ops';

export const MAX_UPLOAD_BYTES = 500 * 1024 * 1024;

export function canHash(): boolean {
  return typeof crypto !== 'undefined' && !!crypto.subtle && typeof crypto.subtle.digest === 'function';
}

export function toHex(buf: ArrayBuffer): string {
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

export interface UploadedMedia {
  id: number;
  kind: string;
  duration_ms: number | null;
}

export async function uploadVideo(file: File, projectId: number | null, onProgress?: (fraction: number) => void): Promise<UploadedMedia> {
  if (!canHash()) throw new Error('Trình duyệt chỉ tính được mã kiểm (sha256) khi mở app qua https hoặc localhost.');
  if (file.size > MAX_UPLOAD_BYTES) throw new Error('File lớn hơn 500 MB — cắt ngắn video mẫu rồi tải lại.');
  const st = (await callOp('upload.start', { name: file.name, size: file.size, kind: 'video', project_id: projectId })) as unknown as {
    id: string;
    received: number;
    chunk_max: number;
  };
  let offset = st.received;
  const chunk = Math.max(1, st.chunk_max || 8 * 1024 * 1024);
  while (offset < file.size) {
    const part = file.slice(offset, Math.min(file.size, offset + chunk));
    await api.put(`/api/upload/${encodeURIComponent(st.id)}?offset=${offset}`, part, {
      headers: { 'Content-Type': 'application/octet-stream' },
    });
    offset += part.size;
    onProgress?.(offset / file.size);
  }
  const sha = toHex(await crypto.subtle.digest('SHA-256', await file.arrayBuffer()));
  return (await callOp('upload.finish', { id: st.id, sha256: sha })) as unknown as UploadedMedia;
}
