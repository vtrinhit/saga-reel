import { Check, Columns2, ImagePlus, Maximize2, RefreshCw } from 'lucide-react';
import { useState } from 'react';
import { callOp } from '../../api/ops';
import { cn } from '../../lib/cn';
import { isTakeRunning, mediaUrl, viewLabel, type Look, type SheetTake } from '../../lib/library';

const isReady = (t: SheetTake) => t.status === 'succeeded' && t.media_id != null;
import { MediaViewer } from '../media/MediaViewer';
import { Button } from '../ui/Button';
import { Modal } from '../ui/Modal';
import { StatusPill } from '../ui/StatusPill';
import { MediaThumb } from './MediaThumb';
import { TakeBadge } from './SheetGrid';
import { useLibMutation } from './useLibrary';

const STATUS_PILL: Record<string, 'queued' | 'running' | 'failed' | 'done'> = {
  queued: 'queued',
  running: 'running',
  failed: 'failed',
  succeeded: 'done',
};

/**
 * Các take của một góc sheet: chọn take (`sheet.select`), so sánh hai take cạnh nhau, tạo lại góc (qua ước tính).
 * Góc khác mỏ neo chỉ tham chiếu ảnh mỏ neo → chưa có mỏ neo thì không tạo lại được.
 */
export function TakesDialog({
  look,
  view,
  takes: rawTakes,
  hasAnchor,
  onClose,
  onRegenerate,
  regenerating,
}: {
  look: Look;
  view: string;
  takes: SheetTake[];
  hasAnchor: boolean;
  onClose: () => void;
  onRegenerate: () => void;
  regenerating?: boolean;
}) {
  const [compare, setCompare] = useState<number[]>([]);
  /** Take đang xem lớn (chỉ số trong các take đã xong). */
  const [big, setBig] = useState<number | null>(null);
  // Hai take trùng ảnh (cùng media) đều được server đánh `selected` — chỉ coi take đầu là đang dùng.
  const selId = rawTakes.find((t) => t.selected)?.id ?? null;
  const takes = rawTakes.map((t) => (t.selected && t.id !== selId ? { ...t, selected: false } : t));
  const select = useLibMutation(
    (t: SheetTake) => callOp('sheet.select', { look_id: look.id, view, take_id: t.id }),
    {
      fail: 'Không chọn được take này.',
      success: (_r, t) => `Đã chọn T${takes.indexOf(t) + 1} cho góc ${viewLabel(view)}.`,
    },
  );
  const toggleCompare = (id: number) =>
    setCompare((c) => (c.includes(id) ? c.filter((x) => x !== id) : [...c, id].slice(-2)));
  const pair = compare.map((id) => takes.find((t) => t.id === id)).filter((t): t is SheetTake => !!t?.media_id);
  const blocked = view !== 'anchor' && !hasAnchor;
  const readyTakes = takes.filter(isReady);
  const bigTake = big != null ? readyTakes[big] : null;

  return (
    <Modal
      open
      size="lg"
      title={`${viewLabel(view)} · ${look.name}`}
      onClose={onClose}
      actions={
        <>
          <Button onClick={onClose}>Đóng</Button>
          <Button
            icon={takes.length ? RefreshCw : ImagePlus}
            onClick={onRegenerate}
            loading={regenerating}
            disabled={blocked}
            title={blocked ? 'Chọn ảnh mỏ neo trước: mọi góc khác chỉ tham chiếu mỏ neo.' : undefined}
          >
            {takes.length ? 'Tạo thêm take' : 'Tạo góc này'}
          </Button>
        </>
      }
    >
      <div className="grid gap-4">
        {blocked ? (
          <p className="text-sm text-fg-2">Look này chưa có ảnh mỏ neo. Tạo và chọn mỏ neo trước — góc này chỉ tham chiếu mỏ neo.</p>
        ) : null}

        {pair.length === 2 ? (
          <section aria-label="So sánh hai take" className="grid grid-cols-2 gap-2">
            {pair.map((t) => (
              <figure key={t.id} className="grid min-w-0 gap-1">
                <button type="button" onClick={() => setBig(readyTakes.indexOf(t))} aria-label={`Xem lớn take ${takes.indexOf(t) + 1}`} className="block rounded-sm focus-visible:outline focus-visible:outline-focus focus-visible:outline-offset-focus focus-visible:outline-accent">
                  <MediaThumb mediaId={t.media_id} alt={`Take ${takes.indexOf(t) + 1}`} width={640} fit="contain" className="aspect-[3/4] w-full rounded-sm" />
                </button>
                <figcaption className="flex items-center justify-between gap-2 text-xs text-fg-2">
                  <TakeBadge n={takes.indexOf(t) + 1} selected={t.selected} />
                  {!t.selected ? (
                    <Button variant="ghost" onClick={() => select.mutate(t)} loading={select.isPending && select.variables?.id === t.id}>
                      Chọn take này
                    </Button>
                  ) : (
                    <span>đang dùng</span>
                  )}
                </figcaption>
              </figure>
            ))}
          </section>
        ) : null}

        {takes.length === 0 ? (
          <p className="text-sm text-fg-2">Góc này chưa có take nào.</p>
        ) : (
          <>
            <p className="flex items-center gap-1.5 text-xs text-fg-3">
              <Columns2 className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden />
              Đánh dấu hai take để so sánh cạnh nhau.
            </p>
            <ul className="grid grid-cols-[repeat(auto-fill,minmax(120px,1fr))] gap-2">
              {takes.map((t, i) => {
                const ready = t.status === 'succeeded' && t.media_id != null;
                const inCompare = compare.includes(t.id);
                return (
                  <li key={t.id} className="grid min-w-0 content-start gap-1.5">
                    <div className={cn('relative overflow-hidden rounded-sm border', t.selected ? 'border-accent' : 'border-line-1')}>
                      <MediaThumb mediaId={ready ? t.media_id : null} alt={`Take ${i + 1}`} width={260} className="aspect-[3/4] w-full" />
                      {ready ? (
                        <button
                          type="button"
                          onClick={() => setBig(readyTakes.indexOf(t))}
                          aria-label={`Xem lớn take ${i + 1}`}
                          className="absolute inset-0 grid place-items-end p-1.5 text-fg-1 focus-visible:outline focus-visible:outline-focus focus-visible:outline-offset-[-2px] focus-visible:outline-accent"
                        >
                          <span className="sr-pill-on-media grid h-6 w-6 place-items-center rounded-sm">
                            <Maximize2 className="h-3.5 w-3.5" aria-hidden />
                          </span>
                        </button>
                      ) : null}
                      <span className="absolute left-1.5 top-1.5">
                        <TakeBadge n={i + 1} selected={t.selected} />
                      </span>
                      {!ready ? (
                        <span className="absolute bottom-1.5 left-1.5">
                          <StatusPill status={STATUS_PILL[t.status] ?? 'queued'} onMedia />
                        </span>
                      ) : null}
                    </div>
                    {ready ? (
                      <div className="flex flex-wrap items-center gap-1">
                        <label className="inline-flex min-h-touch cursor-pointer items-center gap-1.5 text-xs text-fg-2 md:min-h-0">
                          <input
                            type="checkbox"
                            className="h-4 w-4 accent-accent"
                            checked={inCompare}
                            onChange={() => toggleCompare(t.id)}
                          />
                          So sánh
                        </label>
                        {!t.selected ? (
                          <Button
                            variant="ghost"
                            className="ml-auto h-7 px-2 text-xs"
                            onClick={() => select.mutate(t)}
                            loading={select.isPending && select.variables?.id === t.id}
                          >
                            Chọn
                          </Button>
                        ) : (
                          <span className="ml-auto text-xs text-accent">đang dùng</span>
                        )}
                      </div>
                    ) : isTakeRunning(t) ? (
                      <span className="text-xs text-fg-3">sẽ tự hiện khi xong</span>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          </>
        )}
      </div>
      <MediaViewer
        open={bigTake != null}
        onClose={() => setBig(null)}
        title={bigTake ? `${viewLabel(view)} · ${look.name} · Take ${takes.indexOf(bigTake) + 1}` : ''}
        index={big ?? 0}
        count={readyTakes.length}
        onNavigate={setBig}
        stage={
          bigTake ? <img src={mediaUrl(bigTake.media_id!)} alt={`Take ${takes.indexOf(bigTake) + 1} của góc ${viewLabel(view)}`} decoding="async" /> : null
        }
        aside={
          bigTake ? (
            <div className="grid gap-3 p-4">
              {bigTake.selected ? (
                <p className="flex items-center gap-1.5 text-sm text-accent">
                  <Check className="sr-ico" aria-hidden />
                  Đang dùng cho góc này
                </p>
              ) : (
                <Button variant="primary" icon={Check} loading={select.isPending} onClick={() => select.mutate(bigTake)}>
                  Dùng take này
                </Button>
              )}
              <p className="text-xs text-fg-2">←/→ để xem take khác. Esc để đóng.</p>
            </div>
          ) : null
        }
      />
    </Modal>
  );
}
