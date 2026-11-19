import { cn } from '../../lib/cn';
import type { Take } from '../../lib/episodeTypes';
import { thumbUrl } from './ClipCard';

/**
 * Dải take (DESIGN §5.2): thumbnail cao 72px, số take mono dưới ảnh, take đang chọn có vòng khoanh accent. Cuộn ngang
 * trong khung; `↑/↓` (hoặc ←/→) trong dải đổi take.
 */
export function TakeStrip({
  takes,
  selectedId,
  onSelect,
  label = 'Take',
  busyId,
}: {
  takes: Take[];
  selectedId: number | null;
  onSelect: (t: Take) => void;
  label?: string;
  busyId?: number | null;
}) {
  // mới nhất trước từ server → số take theo thứ tự tạo (T1 = cũ nhất)
  const numbered = takes.map((t, i) => ({ t, no: takes.length - i }));
  return (
    <div role="listbox" aria-label={label} aria-orientation="horizontal" className="relative flex min-w-0 gap-2 overflow-x-auto pb-1">
      {numbered.map(({ t, no }) => {
        const sel = t.id === selectedId;
        const ready = t.status === 'succeeded' && t.media_id != null;
        return (
          <button
            key={t.id}
            type="button"
            role="option"
            aria-selected={sel}
            disabled={!ready || busyId === t.id}
            aria-label={`Take ${no}${sel ? ', đang chọn' : ''}${ready ? '' : ` (${t.status === 'failed' ? 'lỗi' : 'đang tạo'})`}`}
            onClick={() => onSelect(t)}
            onKeyDown={(e) => {
              if (['ArrowRight', 'ArrowDown', 'ArrowLeft', 'ArrowUp'].includes(e.key)) {
                e.preventDefault();
                const sib = (e.key === 'ArrowRight' || e.key === 'ArrowDown'
                  ? e.currentTarget.nextElementSibling
                  : e.currentTarget.previousElementSibling) as HTMLElement | null;
                sib?.focus();
              }
            }}
            className={cn(
              'relative grid flex-none gap-1 rounded-sm p-0.5 text-center focus-visible:outline focus-visible:outline-focus focus-visible:outline-offset-focus focus-visible:outline-accent',
              sel ? 'bg-bg-3' : 'hover:bg-bg-3',
            )}
          >
            <span className={cn('relative block h-[72px] w-[41px] overflow-hidden rounded-xs bg-stage', sel && 'ring-2 ring-accent')}>
              {ready ? (
                <img src={thumbUrl(t.media_id, 96)!} alt="" loading="lazy" className="h-full w-full object-cover" />
              ) : (
                <span className={cn('grid h-full place-items-center text-2xs', t.status === 'failed' ? 'text-state-failed' : 'text-fg-3')}>
                  {t.status === 'failed' ? 'Lỗi' : '…'}
                </span>
              )}
            </span>
            <span
              className={cn(
                'mx-auto rounded-full px-1 font-mono text-2xs tnum',
                sel ? 'border border-accent text-accent' : 'text-fg-2',
              )}
            >
              T{no}
            </span>
          </button>
        );
      })}
      {takes.length === 0 ? <p className="text-xs text-fg-3">Chưa có take nào.</p> : null}
    </div>
  );
}
