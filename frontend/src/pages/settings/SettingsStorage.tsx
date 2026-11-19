import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Trash2 } from 'lucide-react';
import { useState } from 'react';
import { useIsViewer } from '../../api/auth';
import { callOp } from '../../api/ops';
import { Button } from '../../components/ui/Button';
import { ErrorState } from '../../components/ui/ErrorState';
import { SkeletonRows, SkeletonZone } from '../../components/ui/Skeleton';
import { errorMessage } from '../../lib/activity';
import { toast } from '../../stores/toast';
import { SectionHeader } from './SettingsLayout';

type Scope = 'segments' | 'chunks' | 'work' | 'all';
interface Bucket {
  files: number;
  bytes: number;
}
type Stats = Record<'segments' | 'chunks' | 'work' | 'total', Bucket> & { chunk_rows?: number };

const nf = new Intl.NumberFormat('vi-VN', { maximumFractionDigits: 1 });

export function formatBytes(n: number): string {
  if (n < 1024) return `${nf.format(n)} B`;
  const u = ['KB', 'MB', 'GB', 'TB'];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${nf.format(v)} ${u[i]}`;
}

const ROWS: { scope: Exclude<Scope, 'all'>; label: string; hint: string }[] = [
  { scope: 'segments', label: 'Đoạn dựng', hint: 'Đoạn video đã ghép theo clip — dựng lại nhanh hơn khi chỉ sửa một clip.' },
  { scope: 'chunks', label: 'Khối xem trước', hint: 'Khối dựng của bản xem trước và animatic.' },
  { scope: 'work', label: 'File tạm', hint: 'File trung gian của ffmpeg; an toàn để xoá khi không có việc đang dựng.' },
];

/**
 * Cài đặt › Lưu trữ (Task M): dung lượng cache dựng phim theo vùng (`render.cache_stats`) và xoá có xác nhận
 * (`render.cache_clear_preview` → `render.cache_clear`). Cache luôn tạo lại được; xoá chỉ làm lần dựng sau chậm hơn.
 * Cần người điều phối gắn route `settings/storage` + mục "Lưu trữ" trong `SettingsLayout`.
 */
export function SettingsStorage() {
  const qc = useQueryClient();
  const viewer = useIsViewer();
  const [armed, setArmed] = useState<Scope | null>(null);
  const stats = useQuery({
    queryKey: ['settings', 'storage'],
    queryFn: async () => (await callOp('render.cache_stats')) as unknown as Stats,
  });
  const clear = useMutation({
    mutationFn: async (scope: Scope) => {
      const pv = (await callOp('render.cache_clear_preview', { scope })) as unknown as { confirm_token: string };
      return callOp('render.cache_clear', { scope, confirm_token: pv.confirm_token });
    },
    onSuccess: () => {
      toast({ message: 'Đã xoá cache dựng phim. Lần dựng kế tiếp sẽ tạo lại phần cần dùng.', tone: 'success' });
      void qc.invalidateQueries({ queryKey: ['settings', 'storage'] });
    },
    onError: (e) => toast({ message: errorMessage(e, 'Không xoá được cache.'), tone: 'error' }),
  });
  const press = (scope: Scope) => {
    if (armed === scope) {
      setArmed(null);
      clear.mutate(scope);
    } else {
      setArmed(scope);
      window.setTimeout(() => setArmed((a) => (a === scope ? null : a)), 4000);
    }
  };

  return (
    <section className="grid gap-4">
      <SectionHeader
        title="Lưu trữ"
        description="Cache dựng phim trên máy nhà. Xoá luôn an toàn: mọi thứ ở đây tạo lại được từ clip và take đã lưu."
      />
      {stats.isError ? (
        <ErrorState what="Không đọc được dung lượng cache" error={stats.error} onRetry={() => void stats.refetch()} />
      ) : !stats.data ? (
        <SkeletonZone label="Đang đo dung lượng">
          <SkeletonRows count={3} />
        </SkeletonZone>
      ) : (
        <div className="max-w-2xl overflow-hidden rounded-md border border-line-2 bg-bg-1">
          {ROWS.map((r) => {
            const b = stats.data[r.scope];
            return (
              <div key={r.scope} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 border-b border-line-1 px-4 py-3">
                <div className="min-w-0">
                  <p className="flex flex-wrap items-baseline gap-x-3 text-sm">
                    <span className="font-medium">{r.label}</span>
                    <span className="font-mono text-fg-2 tnum">
                      {formatBytes(b?.bytes ?? 0)} · {nf.format(b?.files ?? 0)} file
                    </span>
                  </p>
                  <p className="text-xs text-fg-2">{r.hint}</p>
                </div>
                {viewer ? null : (
                  <Button
                    variant="danger"
                    icon={Trash2}
                    armed={armed === r.scope}
                    disabled={!b?.files}
                    loading={clear.isPending && clear.variables === r.scope}
                    onClick={() => press(r.scope)}
                  >
                    {armed === r.scope ? 'Bấm lần nữa để xoá' : 'Xoá'}
                  </Button>
                )}
              </div>
            );
          })}
          <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 px-4 py-3">
            <p className="text-sm">
              <span className="font-medium">Tổng</span>{' '}
              <span className="font-mono text-fg-2 tnum">
                {formatBytes(stats.data.total?.bytes ?? 0)} · {nf.format(stats.data.total?.files ?? 0)} file
              </span>
            </p>
            {viewer ? null : (
              <Button
                variant="danger"
                icon={Trash2}
                armed={armed === 'all'}
                disabled={!stats.data.total?.files}
                loading={clear.isPending && clear.variables === 'all'}
                onClick={() => press('all')}
              >
                {armed === 'all' ? 'Bấm lần nữa để xoá hết' : 'Xoá toàn bộ cache'}
              </Button>
            )}
          </div>
        </div>
      )}
      <p className="max-w-2xl text-xs text-fg-3">
        Dọn bản xem trước cũ và đổ Thùng rác (xoá vĩnh viễn sau 30 ngày) sẽ có ở đây khi máy chủ hỗ trợ.
      </p>
    </section>
  );
}
