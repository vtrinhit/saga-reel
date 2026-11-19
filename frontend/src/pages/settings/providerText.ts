import type { Connection, HealthStatus, ProviderKind } from '../../api/types';
import type { PillStatus } from '../../components/ui/StatusPill';

export const KIND_ORDER: ProviderKind[] = ['image', 'video', 'llm', 'tts', 'stt'];

export const KIND_LABEL: Record<ProviderKind, string> = {
  image: 'Tạo ảnh',
  video: 'Tạo video',
  llm: 'LLM',
  tts: 'Giọng đọc (TTS)',
  stt: 'Nhận dạng lời (STT)',
};

/** Sức khoẻ kết nối → pill (`ok`/`warn`/`failed` theo DESIGN §4.6) + nhãn. */
export function healthView(c: Pick<Connection, 'health' | 'enabled'>): { pill: PillStatus; label: string } {
  if (!c.enabled) return { pill: 'queued', label: 'Đang tắt' };
  const s = c.health?.status as HealthStatus | undefined;
  switch (s) {
    case 'alive':
      return { pill: 'ok', label: 'Hoạt động' };
    case 'dead':
      return { pill: 'failed', label: 'Không phản hồi' };
    case 'not_configured':
      return { pill: 'warn', label: 'Chưa cấu hình' };
    case 'unknown':
      return { pill: 'warn', label: 'Không rõ' };
    default:
      return { pill: 'queued', label: 'Chưa kiểm tra' };
  }
}
