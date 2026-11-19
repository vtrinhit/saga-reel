/**
 * Upload chia khối (`upload.start` → `PUT /api/upload/{id}?offset=` → `upload.finish`) cho file âm thanh của kho
 * `sound.*`. Nối lại từ `received` của server; sha256 tính bằng Web Crypto.
 */
import { api } from '../api/client';
import { callOpLoose } from '../api/opsLoose';

interface UploadStatus {
  id: string;
  received: number;
  chunk_max: number;
  complete: boolean;
  media_id: number | null;
}

export async function sha256Hex(buf: ArrayBuffer): Promise<string> {
  const d = await crypto.subtle.digest('SHA-256', buf);
  return Array.from(new Uint8Array(d), (b) => b.toString(16).padStart(2, '0')).join('');
}

export async function uploadFile(
  file: File,
  opts: { kind: 'audio' | 'image' | 'video' | 'subtitle' | 'other'; projectId?: number | null; onProgress?: (p: number) => void },
): Promise<{ media_id: number }> {
  const st = await callOpLoose<UploadStatus>('upload.start', {
    name: file.name,
    size: file.size,
    kind: opts.kind,
    project_id: opts.projectId ?? null,
  });
  const chunk = Math.max(256 * 1024, Math.min(st.chunk_max || 8 * 1024 * 1024, 8 * 1024 * 1024));
  let offset = st.received || 0;
  while (offset < file.size) {
    const end = Math.min(file.size, offset + chunk);
    await api.put(`/api/upload/${encodeURIComponent(st.id)}`, file.slice(offset, end), {
      params: { offset },
      headers: { 'Content-Type': 'application/octet-stream' },
    });
    offset = end;
    opts.onProgress?.(offset / file.size);
  }
  const sha = await sha256Hex(await file.arrayBuffer());
  const media = await callOpLoose<{ id: number }>('upload.finish', { id: st.id, sha256: sha });
  return { media_id: media.id };
}
