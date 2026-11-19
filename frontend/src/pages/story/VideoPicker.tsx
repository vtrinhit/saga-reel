import { useQuery, useQueryClient } from '@tanstack/react-query';
import { FileVideo, Upload } from 'lucide-react';
import { useState } from 'react';
import { callOp } from '../../api/ops';
import type { MediaItem } from '../../api/types';
import { Checkbox } from '../../components/ui/Checkbox';
import { ProgressBar } from '../../components/ui/ProgressBar';
import { SkeletonRows } from '../../components/ui/Skeleton';
import { cn } from '../../lib/cn';
import { canHash, uploadVideo } from '../../lib/storyUpload';
import { toast } from '../../stores/toast';

const nf = new Intl.NumberFormat('vi-VN');

function dur(ms: number | null): string {
  if (!ms) return '—';
  const s = Math.round(ms / 1000);
  return s < 60 ? `${s} s` : `${Math.floor(s / 60)} ph ${s % 60} s`;
}

/**
 * Chọn video mẫu đã tải (`media.list kind=video`) hoặc tải mới (chia khối). `multiple` = chọn nhiều (kênh mẫu, ≤ 20);
 * không = chọn một (clone).
 */
export function VideoPicker({
  projectId,
  value,
  onChange,
  multiple,
  disabled,
}: {
  projectId: number;
  value: number[];
  onChange: (ids: number[]) => void;
  multiple?: boolean;
  disabled?: boolean;
}) {
  const qc = useQueryClient();
  const [progress, setProgress] = useState<number | null>(null);
  const media = useQuery({
    queryKey: ['story', 'videos', projectId],
    queryFn: async () => ((await callOp('media.list', { kind: 'video', limit: 100 })) as unknown as { items: MediaItem[] }).items,
  });
  const hashOk = canHash();

  const toggle = (id: number) => {
    if (multiple) onChange(value.includes(id) ? value.filter((v) => v !== id) : [...value, id].slice(0, 20));
    else onChange(value[0] === id ? [] : [id]);
  };

  const onFile = async (f: File | undefined) => {
    if (!f) return;
    setProgress(0);
    try {
      const m = await uploadVideo(f, projectId, setProgress);
      toast({ message: `Đã tải ${f.name}.`, tone: 'success' });
      await qc.invalidateQueries({ queryKey: ['story', 'videos', projectId] });
      onChange(multiple ? [...value, m.id].slice(0, 20) : [m.id]);
    } catch (e) {
      toast({ message: e instanceof Error ? e.message : 'Không tải được video.', tone: 'error' });
    } finally {
      setProgress(null);
    }
  };

  const items = media.data ?? [];
  return (
    <fieldset className="grid min-w-0 gap-2" disabled={disabled}>
      <legend className="mb-1 text-sm font-medium">{multiple ? 'Video mẫu (tối đa 20)' : 'Video mẫu'}</legend>
      {media.isLoading ? (
        <SkeletonRows count={3} />
      ) : items.length === 0 ? (
        <p className="text-sm text-fg-2">Chưa có video nào trong kho. Tải một video mẫu (chỉ để phân tích, không chép khung hình).</p>
      ) : (
        <ul className="grid max-h-64 gap-1 overflow-y-auto rounded-md border border-line-1 p-1">
          {items.map((m) => {
            const on = value.includes(m.id);
            return (
              <li key={m.id}>
                {multiple ? (
                  <Checkbox
                    label={`Video #${m.id} · ${dur(m.duration_ms)} · ${m.width ?? '?'}×${m.height ?? '?'}`}
                    checked={on}
                    onChange={() => toggle(m.id)}
                  />
                ) : (
                  <label className={cn('flex min-h-touch cursor-pointer items-center gap-2 rounded-sm px-2 text-sm hover:bg-bg-3 md:min-h-8', on && 'bg-bg-3')}>
                    <input type="radio" name={`video-pick-${projectId}`} checked={on} onChange={() => toggle(m.id)} className="accent-[rgb(var(--c-accent))]" />
                    <FileVideo className="sr-ico text-fg-3" aria-hidden />
                    <span className="tnum">
                      Video #{m.id} · {dur(m.duration_ms)} · {m.width ?? '?'}×{m.height ?? '?'}
                    </span>
                  </label>
                )}
              </li>
            );
          })}
        </ul>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <label
          className={cn(
            'inline-flex h-control cursor-pointer items-center gap-2 rounded-md border border-line-2 bg-bg-2 px-3 text-sm hover:bg-bg-3',
            'has-[:focus-visible]:outline has-[:focus-visible]:outline-focus has-[:focus-visible]:outline-offset-focus has-[:focus-visible]:outline-accent',
            (!hashOk || progress != null) && 'pointer-events-none text-fg-4',
          )}
        >
          <Upload className="sr-ico" aria-hidden />
          Tải video mẫu
          <input type="file" accept="video/*" className="sr-only" disabled={!hashOk || progress != null} onChange={(e) => void onFile(e.target.files?.[0])} />
        </label>
        {progress != null ? (
          <span className="flex min-w-0 flex-1 items-center gap-2 text-xs text-fg-2 tnum">
            <ProgressBar value={progress * 100} className="w-32" />
            {nf.format(Math.round(progress * 100))}%
          </span>
        ) : null}
        {!hashOk ? <span className="text-xs text-fg-3">Tải lên cần mở app qua https hoặc localhost.</span> : null}
        {value.length ? <span className="text-xs text-fg-2 tnum">Đã chọn {nf.format(value.length)}</span> : null}
      </div>
    </fieldset>
  );
}
