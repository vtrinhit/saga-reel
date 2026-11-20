import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArchiveRestore, FileAudio, FileImage, FileVideo, Trash2, type LucideIcon } from 'lucide-react';
import { useMemo, useState } from 'react';
import { useIsViewer } from '../../api/auth';
import { callOp } from '../../api/ops';
import { Button } from '../../components/ui/Button';
import { Checkbox } from '../../components/ui/Checkbox';
import { ConfirmDelete } from '../../components/ui/ConfirmDelete';
import { EmptyState } from '../../components/ui/EmptyState';
import { ErrorState } from '../../components/ui/ErrorState';
import { SegmentedControl } from '../../components/ui/SegmentedControl';
import { SkeletonRows, SkeletonZone } from '../../components/ui/Skeleton';
import { errorMessage } from '../../lib/activity';
import { relativeFromServer } from '../../lib/time';
import { toast } from '../../stores/toast';
import { SectionHeader } from './SettingsLayout';

type Scope = 'segments' | 'chunks' | 'work' | 'all';
type Kind = 'image' | 'video' | 'audio';
interface Bucket {
  files: number;
  bytes: number;
}
type Stats = Record<'segments' | 'chunks' | 'work' | 'total', Bucket> & { chunk_rows?: number };

/** `media.cleanup_preview` / `trash.purge_preview` (`services/storage.py`). */
interface FilePreview {
  files: number;
  bytes: number;
  by_kind: Partial<Record<Kind, Bucket>>;
  truncated: boolean;
  confirm_token: string;
  warning?: string;
}

/** Mục `trash.list`. */
export interface TrashItem {
  media_id: number;
  kind: Kind | string;
  size: number;
  path: string;
  project_id: number | null;
  trashed_at: string | null;
  file_present: boolean;
}
interface TrashPage {
  items: TrashItem[];
  next_cursor: number | null;
  total: Bucket;
}

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

const KIND: Record<Kind, { label: string; icon: LucideIcon }> = {
  image: { label: 'ảnh', icon: FileImage },
  video: { label: 'video', icon: FileVideo },
  audio: { label: 'âm thanh', icon: FileAudio },
};

/** "12 ảnh · 3 video" từ `by_kind`. */
export function kindSummary(byKind: FilePreview['by_kind']): string {
  return (Object.keys(KIND) as Kind[])
    .filter((k) => byKind[k]?.files)
    .map((k) => `${nf.format(byKind[k]!.files)} ${KIND[k].label}`)
    .join(' · ');
}

const CACHE_ROWS: { scope: Exclude<Scope, 'all'>; label: string; hint: string }[] = [
  { scope: 'segments', label: 'Đoạn dựng', hint: 'Đoạn video đã ghép theo clip — dựng lại nhanh hơn khi chỉ sửa một clip.' },
  { scope: 'chunks', label: 'Khối xem trước', hint: 'Khối dựng của bản xem trước và animatic.' },
  { scope: 'work', label: 'File tạm', hint: 'File trung gian của ffmpeg; an toàn để xoá khi không có việc đang dựng.' },
];

const AGE_OPTIONS = [
  { value: '7', label: '7 ngày' },
  { value: '30', label: '30 ngày' },
  { value: '90', label: '90 ngày' },
];

const baseName = (p: string) => p.split(/[\\/]/).pop() || p;
const errMsg = (e: unknown, fallback: string) => errorMessage(e, fallback);

/* ------------------------------------------------------------------------------------------------------------------ */

function CleanupSection({ viewer }: { viewer: boolean }) {
  const qc = useQueryClient();
  const [age, setAge] = useState('30');
  const [kinds, setKinds] = useState<Set<Kind>>(() => new Set(['image', 'video', 'audio']));
  const filters = { unselected_takes_older_than_days: Number(age), kinds: [...kinds] };
  const preview = useQuery({
    queryKey: ['settings', 'storage', 'cleanup', age, [...kinds].sort().join(',')],
    queryFn: async () => (await callOp('media.cleanup_preview', { filters })) as unknown as FilePreview,
    enabled: !viewer && kinds.size > 0,
    staleTime: 0,
  });
  const run = useMutation({
    mutationFn: async () => {
      // Lấy token mới ngay trước khi dọn: ứng viên đổi giữa hai bước thì server từ chối (confirm_required).
      const pv = (await callOp('media.cleanup_preview', { filters })) as unknown as FilePreview;
      return (await callOp('media.cleanup', { confirm_token: pv.confirm_token })) as unknown as {
        files: number;
        bytes: number;
        skipped?: unknown[];
      };
    },
    onSuccess: (r) => {
      const skipped = r.skipped?.length ? ` Bỏ qua ${nf.format(r.skipped.length)} file đang mở.` : '';
      toast({
        message: `Đã chuyển ${nf.format(r.files)} file (${formatBytes(r.bytes)}) vào thùng rác.${skipped} Khôi phục được ở mục Thùng rác.`,
        tone: 'success',
      });
      void qc.invalidateQueries({ queryKey: ['settings', 'storage'] });
    },
    onError: (e) => toast({ message: errMsg(e, 'Không dọn được file.'), tone: 'error' }),
  });
  const toggle = (k: Kind) =>
    setKinds((s) => {
      const n = new Set(s);
      if (n.has(k)) n.delete(k);
      else n.add(k);
      return n;
    });
  const p = preview.data;

  return (
    <section aria-labelledby="cleanup-h" className="grid gap-3">
      <div className="grid gap-1">
        <h3 id="cleanup-h" className="text-md font-semibold">Dọn take không được chọn</h3>
        <p className="max-w-prose text-sm text-fg-2">
          Chuyển file gốc của các take bạn không chọn sang thùng rác. Take vẫn còn trong lịch sử clip; file đang dùng ở
          bất kỳ đâu (look, bản dựng, timeline…) không bị đụng tới.
        </p>
      </div>
      {viewer ? (
        <p className="text-sm text-fg-2">Người xem không dọn được file.</p>
      ) : (
        <div className="grid max-w-2xl gap-3 rounded-md border border-line-2 bg-bg-1 p-4">
          <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
            <div className="grid gap-1">
              <span className="text-xs text-fg-2" id="cleanup-age">Take cũ hơn</span>
              <SegmentedControl label="Take cũ hơn" options={AGE_OPTIONS} value={age} onChange={setAge} />
            </div>
            <fieldset className="flex flex-wrap items-center gap-x-4">
              <legend className="sr-only">Loại file</legend>
              {(Object.keys(KIND) as Kind[]).map((k) => (
                <Checkbox key={k} label={KIND[k].label[0].toUpperCase() + KIND[k].label.slice(1)} checked={kinds.has(k)} onChange={() => toggle(k)} />
              ))}
            </fieldset>
          </div>
          <div className="flex min-h-control flex-wrap items-center justify-between gap-3 border-t border-line-1 pt-3" aria-live="polite">
            {kinds.size === 0 ? (
              <p className="text-sm text-fg-2">Chọn ít nhất một loại file.</p>
            ) : preview.isError ? (
              <p className="text-sm text-state-failed">{errMsg(preview.error, 'Không xem trước được.')}</p>
            ) : !p ? (
              <p className="text-sm text-fg-2">Đang tìm file có thể dọn…</p>
            ) : p.files === 0 ? (
              <p className="text-sm text-fg-2">Không có file nào để dọn với điều kiện này.</p>
            ) : (
              <p className="text-sm">
                <span className="font-mono tnum">{nf.format(p.files)} file · {formatBytes(p.bytes)}</span>
                <span className="text-fg-2"> — {kindSummary(p.by_kind)}{p.truncated ? ' (danh sách đã rút gọn)' : ''}</span>
              </p>
            )}
            <ConfirmDelete
              what={p ? `${nf.format(p.files)} file` : 'file'}
              label="Chuyển vào thùng rác"
              armedLabel={p ? `Bấm lần nữa để dọn ${nf.format(p.files)} file` : undefined}
              icon={Trash2}
              disabled={!p?.files || run.isPending}
              loading={run.isPending}
              onConfirm={() => run.mutateAsync().then(() => undefined)}
            />
          </div>
        </div>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------------------------------------------------------ */

function TrashSection({ viewer }: { viewer: boolean }) {
  const qc = useQueryClient();
  const [sel, setSel] = useState<Set<number>>(() => new Set());
  const [purgeAge, setPurgeAge] = useState<'0' | '30'>('30');
  const trash = useInfiniteQuery({
    queryKey: ['settings', 'storage', 'trash'],
    initialPageParam: null as number | null,
    queryFn: async ({ pageParam }) =>
      (await callOp('trash.list', { cursor: pageParam ?? undefined, limit: 100 })) as unknown as TrashPage,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    enabled: !viewer,
  });
  const items = useMemo(() => trash.data?.pages.flatMap((p) => p.items) ?? [], [trash.data]);
  const total = trash.data?.pages[0]?.total;
  const purgePreview = useQuery({
    queryKey: ['settings', 'storage', 'purge', purgeAge],
    queryFn: async () => (await callOp('trash.purge_preview', { older_than_days: Number(purgeAge) })) as unknown as FilePreview,
    enabled: !viewer && (total?.files ?? 0) > 0,
    staleTime: 0,
  });
  const refresh = () => void qc.invalidateQueries({ queryKey: ['settings', 'storage'] });
  const restore = useMutation({
    mutationFn: async (ids: number[]) =>
      (await callOp('trash.restore', { media_ids: ids })) as unknown as { restored?: number[]; skipped?: unknown[] },
    onSuccess: (r, ids) => {
      const n = r.restored?.length ?? ids.length;
      const skipped = r.skipped?.length ? ` ${nf.format(r.skipped.length)} file không khôi phục được (đang mở hoặc đã mất).` : '';
      toast({ message: `Đã khôi phục ${nf.format(n)} file về chỗ cũ.${skipped}`, tone: skipped ? 'info' : 'success' });
      setSel(new Set());
      refresh();
    },
    onError: (e) => toast({ message: errMsg(e, 'Không khôi phục được.'), tone: 'error' }),
  });
  const purge = useMutation({
    mutationFn: async () => {
      const pv = (await callOp('trash.purge_preview', { older_than_days: Number(purgeAge) })) as unknown as FilePreview;
      return (await callOp('trash.purge', { confirm_token: pv.confirm_token })) as unknown as { files: number; bytes: number };
    },
    onSuccess: (r) => {
      toast({ message: `Đã xoá hẳn ${nf.format(r.files)} file, giải phóng ${formatBytes(r.bytes)}.`, tone: 'success' });
      setSel(new Set());
      refresh();
    },
    onError: (e) => toast({ message: errMsg(e, 'Không xoá được.'), tone: 'error' }),
  });
  const toggle = (id: number) =>
    setSel((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  const pp = purgePreview.data;

  return (
    <section aria-labelledby="trash-h" className="grid gap-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="grid gap-1">
          <h3 id="trash-h" className="text-md font-semibold">Thùng rác</h3>
          <p className="max-w-prose text-sm text-fg-2">
            {total ? `${nf.format(total.files)} file · ${formatBytes(total.bytes)}. ` : ''}File ở đây khôi phục được về đúng chỗ
            cũ cho tới khi bạn xoá hẳn.
          </p>
        </div>
      </div>
      {viewer ? (
        <p className="text-sm text-fg-2">Người xem không xem được thùng rác.</p>
      ) : trash.isError ? (
        <ErrorState what="Không đọc được thùng rác." error={trash.error} onRetry={() => void trash.refetch()} />
      ) : trash.isPending ? (
        <SkeletonZone label="Đang tải thùng rác">
          <SkeletonRows count={3} />
        </SkeletonZone>
      ) : items.length === 0 ? (
        <EmptyState icon={Trash2} title="Thùng rác trống." description="Take không được chọn mà bạn dọn sẽ nằm ở đây trước khi bị xoá hẳn." className="max-w-2xl rounded-md border border-line-2 bg-bg-1" />
      ) : (
        <div className="grid max-w-2xl gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <Checkbox
              label={sel.size ? `Đã chọn ${nf.format(sel.size)}` : 'Chọn tất cả đã tải'}
              checked={sel.size > 0 && sel.size === items.length}
              ref={(el) => {
                if (el) el.indeterminate = sel.size > 0 && sel.size < items.length;
              }}
              onChange={() => setSel(sel.size === items.length ? new Set() : new Set(items.map((i) => i.media_id)))}
            />
            <Button icon={ArchiveRestore} disabled={sel.size === 0} loading={restore.isPending} onClick={() => restore.mutate([...sel])} className="ml-auto">
              Khôi phục{sel.size ? ` ${nf.format(sel.size)} file` : ''}
            </Button>
          </div>
          <ul className="max-h-[420px] overflow-y-auto rounded-md border border-line-2 bg-bg-1">
            {items.map((it) => {
              const k = KIND[it.kind as Kind];
              const Icon = k?.icon ?? FileImage;
              return (
                <li key={it.media_id} className="border-b border-line-1 last:border-b-0">
                  <label className="grid cursor-pointer grid-cols-[auto_auto_minmax(0,1fr)_auto] items-center gap-3 px-3 py-2 hover:bg-bg-2">
                    <input
                      type="checkbox"
                      className="m-0 h-[18px] w-[18px] accent-accent"
                      checked={sel.has(it.media_id)}
                      onChange={() => toggle(it.media_id)}
                      aria-label={`Chọn ${baseName(it.path)}`}
                    />
                    <Icon className="sr-ico text-fg-2" aria-hidden />
                    <span className="grid min-w-0">
                      <span className="truncate text-sm">{baseName(it.path)}</span>
                      <span className="truncate text-xs text-fg-2">
                        {k?.label ?? it.kind}
                        {it.trashed_at ? ` · vào thùng rác ${relativeFromServer(it.trashed_at)}` : ''}
                        {it.file_present ? '' : ' · file đã mất'}
                      </span>
                    </span>
                    <span className="font-mono text-xs text-fg-2 tnum">{formatBytes(it.size)}</span>
                  </label>
                </li>
              );
            })}
          </ul>
          {trash.hasNextPage ? (
            <Button variant="ghost" loading={trash.isFetchingNextPage} onClick={() => void trash.fetchNextPage()} className="justify-self-start">
              Tải thêm
            </Button>
          ) : null}
          <div className="grid gap-2 rounded-md border border-state-failed/40 bg-bg-1 p-4">
            <h4 className="text-sm font-semibold">Xoá hẳn khỏi đĩa</h4>
            <p className="text-xs text-fg-2">Không hoàn tác được. Take vẫn còn trong lịch sử nhưng không xem lại được file.</p>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <SegmentedControl<'0' | '30'>
                label="Xoá file nào"
                options={[
                  { value: '30', label: 'Đã nằm ≥ 30 ngày' },
                  { value: '0', label: 'Tất cả' },
                ]}
                value={purgeAge}
                onChange={setPurgeAge}
              />
              <span className="font-mono text-sm tnum" aria-live="polite">
                {pp ? `${nf.format(pp.files)} file · ${formatBytes(pp.bytes)}` : '…'}
              </span>
            </div>
            <ConfirmDelete
              what={pp ? `${nf.format(pp.files)} file trong thùng rác` : 'file trong thùng rác'}
              label="Xoá hẳn"
              icon={Trash2}
              className="justify-self-start"
              disabled={!pp?.files || purge.isPending}
              loading={purge.isPending}
              strong={{
                consequence: `${pp ? nf.format(pp.files) : 0} file (${formatBytes(pp?.bytes ?? 0)}) bị xoá khỏi đĩa. Không khôi phục được.`,
                typeToConfirm: 'xoá hẳn',
              }}
              onConfirm={() => purge.mutateAsync().then(() => undefined)}
            />
          </div>
        </div>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------------------------------------------------------ */

function CacheSection({ viewer }: { viewer: boolean }) {
  const qc = useQueryClient();
  const stats = useQuery({
    queryKey: ['settings', 'storage', 'cache'],
    queryFn: async () => (await callOp('render.cache_stats')) as unknown as Stats,
  });
  const clear = useMutation({
    mutationFn: async (scope: Scope) => {
      const pv = (await callOp('render.cache_clear_preview', { scope })) as unknown as { confirm_token: string };
      return callOp('render.cache_clear', { scope, confirm_token: pv.confirm_token });
    },
    onSuccess: () => {
      toast({ message: 'Đã xoá cache dựng phim. Lần dựng kế tiếp sẽ tạo lại phần cần dùng.', tone: 'success' });
      void qc.invalidateQueries({ queryKey: ['settings', 'storage', 'cache'] });
    },
    onError: (e) => toast({ message: errMsg(e, 'Không xoá được cache.'), tone: 'error' }),
  });
  const clearBtn = (scope: Scope, what: string, files: number | undefined) =>
    viewer ? null : (
      <ConfirmDelete
        what={what}
        icon={Trash2}
        disabled={!files}
        loading={clear.isPending && clear.variables === scope}
        onConfirm={() => clear.mutateAsync(scope).then(() => undefined)}
      />
    );

  return (
    <section aria-labelledby="cache-h" className="grid gap-3">
      <div className="grid gap-1">
        <h3 id="cache-h" className="text-md font-semibold">Cache dựng phim</h3>
        <p className="max-w-prose text-sm text-fg-2">Xoá luôn an toàn: mọi thứ ở đây tạo lại được từ clip và take đã lưu.</p>
      </div>
      {stats.isError ? (
        <ErrorState what="Không đọc được dung lượng cache." error={stats.error} onRetry={() => void stats.refetch()} />
      ) : !stats.data ? (
        <SkeletonZone label="Đang đo dung lượng">
          <SkeletonRows count={3} />
        </SkeletonZone>
      ) : (
        <div className="max-w-2xl overflow-hidden rounded-md border border-line-2 bg-bg-1">
          {CACHE_ROWS.map((r) => {
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
                {clearBtn(r.scope, r.label.toLowerCase(), b?.files)}
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
            {clearBtn('all', 'toàn bộ cache', stats.data.total?.files)}
          </div>
        </div>
      )}
    </section>
  );
}

/**
 * Cài đặt › Lưu trữ: dọn file gốc của take không được chọn sang thùng rác (`media.cleanup_preview` → `media.cleanup`),
 * thùng rác (`trash.list` / `trash.restore` / `trash.purge_preview` → `trash.purge`), cache dựng phim
 * (`render.cache_stats`, `render.cache_clear_preview` → `render.cache_clear`).
 */
export function SettingsStorage() {
  const viewer = useIsViewer();
  return (
    <section className="grid gap-8">
      <SectionHeader
        title="Lưu trữ"
        description="Giải phóng ổ đĩa của máy nhà. Dọn take thì vào thùng rác trước; chỉ bước xoá hẳn là không quay lại được."
      />
      <CleanupSection viewer={viewer} />
      <TrashSection viewer={viewer} />
      <CacheSection viewer={viewer} />
    </section>
  );
}
