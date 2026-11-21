import { Sparkles } from 'lucide-react';
import { useEffect, useId, useMemo, useState } from 'react';
import { useAdapters, useConnections } from '../../api/hooks';
import { cn, focusRing } from '../../lib/cn';
import type { Clip } from '../../lib/episodeTypes';
import { Button } from '../ui/Button';
import { Modal } from '../ui/Modal';
import { SegmentedControl } from '../ui/SegmentedControl';

export interface ProviderChoice {
  part: 'image' | 'video';
  /** Tên kết nối ('' = theo cài đặt). */
  connection: string;
  model: string;
  /** Ghi vào clip (`clip.connection/model`) để lần sau tự dùng. */
  remember: boolean;
}

const field = cn('h-control w-full min-w-0 rounded-sm border border-line-3 bg-bg-2 px-2 text-sm text-fg-1', focusRing);

/**
 * Chọn provider + model cho một lần tạo của clip. Kết nối lấy từ `provider.list` theo loại; model gợi ý từ khả năng của
 * adapter (`default_capabilities.models`), vẫn gõ tay được. Chạy = qua ước tính (EstimateDialog) như mọi việc trả phí.
 */
export function ProviderDialog({
  clip, open, onClose, onRun,
}: {
  clip: Clip | null;
  open: boolean;
  onClose: () => void;
  onRun: (choice: ProviderChoice) => void;
}) {
  const conns = useConnections();
  const adapters = useAdapters();
  const hasImage = clip ? clip.image_take_id != null || clip.image_media_id != null : false;
  const [part, setPart] = useState<'image' | 'video'>('image');
  const [connection, setConnection] = useState('');
  const [model, setModel] = useState('');
  const [remember, setRemember] = useState(false);
  const id = useId();
  useEffect(() => {
    if (!open || !clip) return;
    setPart(hasImage && clip.video_take_id == null ? 'video' : 'image');
    setConnection(clip.connection ?? '');
    setModel(clip.model ?? '');
    setRemember(false);
  }, [open, clip, hasImage]);

  const list = useMemo(() => (conns.data ?? []).filter((c) => c.kind === part), [conns.data, part]);
  const conn = list.find((c) => c.name === connection);
  // Kết nối riêng đã lưu của clip thuộc loại khác → coi như "theo cài đặt" cho loại này.
  useEffect(() => {
    if (connection && conns.data && !list.some((c) => c.name === connection)) setConnection('');
  }, [connection, conns.data, list]);
  const models = useMemo(() => {
    const ad = (adapters.data ?? []).find((a) => a.id === conn?.adapter);
    const m = ad?.default_capabilities?.models;
    return Array.isArray(m) ? (m as string[]) : [];
  }, [adapters.data, conn]);
  const disabledPicked = conn != null && !conn.enabled;

  return (
    <Modal
      open={open && !!clip}
      onClose={onClose}
      title={clip ? `Tạo bằng provider khác · ${clip.key}` : 'Provider'}
      actions={
        <>
          <Button onClick={onClose}>Huỷ</Button>
          <Button
            variant="primary"
            icon={Sparkles}
            disabled={disabledPicked || (part === 'video' && !hasImage)}
            onClick={() => onRun({ part, connection, model: part === 'image' ? model.trim() : '', remember })}
          >
            Xem báo giá
          </Button>
        </>
      }
    >
      <div className="grid gap-3">
        <SegmentedControl<'image' | 'video'>
          label="Phần cần tạo"
          options={[
            { value: 'image', label: 'Ảnh' },
            { value: 'video', label: 'Video', disabled: !hasImage },
          ]}
          value={part}
          onChange={setPart}
        />
        {part === 'video' && !hasImage ? <p className="text-xs text-fg-2">Video tạo từ ảnh: tạo ảnh trước.</p> : null}
        <label className="grid gap-1 text-sm" htmlFor={`${id}-c`}>
          Kết nối
          <select id={`${id}-c`} className={field} value={connection} onChange={(e) => setConnection(e.target.value)}>
            <option value="">Theo cài đặt dự án</option>
            {list.map((c) => (
              <option key={c.id} value={c.name}>
                {c.name}
                {c.enabled ? '' : ' (đang tắt)'}
              </option>
            ))}
          </select>
        </label>
        {disabledPicked ? (
          <p role="alert" className="text-xs text-state-failed">
            Kết nối này đang tắt. Bật trong Cài đặt › Provider hoặc chọn kết nối khác.
          </p>
        ) : null}
        {part === 'image' ? (
          <label className="grid gap-1 text-sm" htmlFor={`${id}-m`}>
            Model
            <input
              id={`${id}-m`}
              className={field}
              list={`${id}-ml`}
              value={model}
              placeholder={conn?.default_model ? `mặc định: ${conn.default_model}` : 'mặc định của kết nối'}
              onChange={(e) => setModel(e.target.value)}
            />
            <datalist id={`${id}-ml`}>
              {models.map((m) => (
                <option key={m} value={m} />
              ))}
            </datalist>
          </label>
        ) : null}
        <label className="flex min-h-touch items-center gap-2 text-sm md:min-h-0">
          <input type="checkbox" className="h-4 w-4 accent-[rgb(var(--c-accent))]" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
          Nhớ cho clip này ({part === 'image' ? 'ảnh' : 'video'})
        </label>
      </div>
    </Modal>
  );
}
