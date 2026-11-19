import { CircleAlert, Copy } from 'lucide-react';
import { useState } from 'react';
import { viewLabel, type Look } from '../../lib/library';
import { toast } from '../../stores/toast';
import { IconButton } from '../ui/IconButton';
import { Select } from '../ui/Select';
import { SkeletonRows } from '../ui/Skeleton';
import { MediaThumb } from './MediaThumb';
import { errText, usePromptPreview } from './useLibrary';

const ROLE_LABEL: Record<string, string> = {
  appearance: 'ngoại hình',
  outfit: 'trang phục',
  spatial_layout: 'bố cục không gian',
  composition: 'bố cục',
  scale: 'tỉ lệ',
  effect: 'hiệu ứng',
  start_frame: 'khung đầu',
  end_frame: 'khung cuối',
  style: 'phong cách',
  subject: 'chủ thể',
  anchor: 'mỏ neo',
};

/**
 * "Prompt cuối" của một góc sheet (`look.preview_prompt`): đúng chữ sẽ gửi provider (mono, chỉ đọc), ảnh tham chiếu có
 * nhãn theo thứ tự gửi, cảnh báo khả năng provider (vd. không nhận ảnh ref thứ 2).
 */
export function PromptFinal({ look, views }: { look: Look; views: string[] }) {
  const [view, setView] = useState(views[0] ?? 'anchor');
  const q = usePromptPreview(look.id, view, true);
  const p = q.data;
  const fid = `prompt-${look.id}`;
  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-end gap-2">
        <Select
          id={`${fid}-view`}
          label="Góc"
          value={view}
          options={views.map((v) => ({ value: v, label: viewLabel(v) }))}
          onChange={setView}
          className="w-44"
        />
        {p ? (
          <IconButton
            icon={Copy}
            label="Sao chép prompt"
            onClick={() => {
              void navigator.clipboard?.writeText(p.text).then(
                () => toast({ message: 'Đã sao chép prompt.', tone: 'success' }),
                () => toast({ message: 'Trình duyệt chặn sao chép — chọn chữ rồi Ctrl C.', tone: 'error' }),
              );
            }}
          />
        ) : null}
      </div>
      {q.isPending ? <SkeletonRows count={3} /> : null}
      {q.isError ? (
        <p className="text-sm text-state-failed">{errText(q.error, 'Không dựng được prompt cho góc này.')}</p>
      ) : null}
      {p ? (
        <>
          {p.warnings.map((w) => (
            <div key={w} className="sr-warnbox flex items-start gap-2 rounded-sm px-2.5 py-2 text-sm">
              <CircleAlert className="sr-ico mt-0.5 text-warn" aria-hidden />
              <span className="min-w-0">{w}</span>
            </div>
          ))}
          <pre
            className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-sm border border-line-1 bg-bg-0 p-2.5 font-mono text-xs text-fg-1"
            aria-label={`Prompt cuối · ${viewLabel(view)}`}
          >
            {p.text}
          </pre>
          <div className="grid gap-1.5">
            <span className="text-sm font-medium">Ảnh tham chiếu gửi kèm</span>
            {p.refs.length ? (
              <ol className="flex flex-wrap gap-2">
                {p.refs.map((r, i) => (
                  <li key={`${r.media_id}-${i}`} className="grid w-20 gap-1">
                    <MediaThumb mediaId={r.media_id} alt={r.label} width={160} className="aspect-[3/4] w-full rounded-xs border border-line-1" />
                    <span className="text-2xs leading-tight text-fg-2">
                      <span className="font-mono text-fg-3">{i + 1}.</span> {r.label || ROLE_LABEL[r.role] || r.role}
                    </span>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="text-xs text-fg-3">
                {view === 'anchor' ? 'Mỏ neo tạo chỉ từ chữ — không gửi ảnh tham chiếu.' : 'Không có ảnh tham chiếu.'}
              </p>
            )}
          </div>
        </>
      ) : null}
    </div>
  );
}
