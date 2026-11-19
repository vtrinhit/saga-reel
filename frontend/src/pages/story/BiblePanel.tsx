import { useMutation, useQueryClient } from '@tanstack/react-query';
import { BookOpen, CheckCheck, Plus, Search } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { callOp } from '../../api/ops';
import { BibleItem } from '../../components/story/BibleItem';
import { Button } from '../../components/ui/Button';
import { Checkbox } from '../../components/ui/Checkbox';
import { EmptyState } from '../../components/ui/EmptyState';
import { ErrorState } from '../../components/ui/ErrorState';
import { Input } from '../../components/ui/Input';
import { Modal } from '../../components/ui/Modal';
import { SegmentedControl } from '../../components/ui/SegmentedControl';
import { Select } from '../../components/ui/Select';
import { SkeletonRows } from '../../components/ui/Skeleton';
import { Textarea } from '../../components/ui/Textarea';
import { errorMessage } from '../../lib/activity';
import { BIBLE_TYPES, sortBible, type BibleItem as Item, type BibleType } from '../../lib/story';
import { toast } from '../../stores/toast';
import { storyKeys, useBible, useBibleItem, useBibleSearch } from './useStory';

const nf = new Intl.NumberFormat('vi-VN');

function useDebounced<T>(v: T, ms: number): T {
  const [d, setD] = useState(v);
  useEffect(() => {
    const t = setTimeout(() => setD(v), ms);
    return () => clearTimeout(t);
  }, [v, ms]);
  return d;
}

/**
 * Cột Bible: tìm FTS không dấu (`bible.search`), lọc chờ xác nhận, bằng chứng trích dẫn, xác nhận / bỏ / khoá, thêm mục.
 * Gõ có dấu hay không dấu đều khớp (server bỏ dấu, đ → d).
 */
export function BiblePanel({ projectId, viewer, seqNumber }: { projectId: number; viewer: boolean; seqNumber: (id: number) => number | null }) {
  const qc = useQueryClient();
  const [q, setQ] = useState('');
  const dq = useDebounced(q.trim(), 250);
  const [filter, setFilter] = useState<'all' | 'pending'>('all');
  const [type, setType] = useState<string>('');
  const [open, setOpen] = useState<number | null>(null);
  const [adding, setAdding] = useState(false);
  const list = useBible(projectId);
  const search = useBibleSearch(projectId, dq, filter === 'pending' ? ['pending'] : null);
  const detail = useBibleItem(open);

  const all = useMemo(() => list.data ?? [], [list.data]);
  const pending = all.filter((i) => i.status === 'pending');
  const searching = dq.length > 0;
  const source = searching ? (search.data?.items ?? []) : sortBible(all.filter((i) => i.status !== 'rejected'));
  const shown = source.filter((i) => (filter === 'pending' ? i.status === 'pending' : true) && (!type || i.type === type));

  const refresh = () => void qc.invalidateQueries({ queryKey: storyKeys.all });
  const setStatus = useMutation({
    mutationFn: (v: { ids: number[]; action: 'confirm' | 'reject' }) =>
      callOp(v.action === 'confirm' ? 'bible.confirm' : 'bible.reject', { ids: v.ids, lock: null }),
    onSuccess: (_r, v) => {
      toast({
        message: v.action === 'confirm' ? `Đã xác nhận ${nf.format(v.ids.length)} mục bible.` : `Đã bỏ ${nf.format(v.ids.length)} mục — LLM không dùng khi viết.`,
        tone: 'success',
      });
      refresh();
    },
    onError: (e) => toast({ message: errorMessage(e, 'Không đổi được trạng thái mục bible.'), tone: 'error' }),
  });
  const lock = useMutation({
    mutationFn: (v: { item: Item; locked: boolean }) =>
      callOp('bible.update', { id: v.item.id, values: { locked: v.locked }, expected_updated_at: null }),
    onSuccess: (_r, v) => {
      toast({ message: v.locked ? 'Đã khoá — LLM không được sửa mục này.' : 'Đã mở khoá mục bible.', tone: 'success' });
      refresh();
    },
    onError: (e) => toast({ message: errorMessage(e, 'Không khoá được mục bible.'), tone: 'error' }),
  });

  const evidenceOf = (it: Item) => (it.evidence ? it.evidence : open === it.id ? detail.data?.evidence : undefined);

  return (
    <section aria-labelledby="bible-h" className="grid min-w-0 content-start rounded-md border border-line-2 bg-bg-1">
      <header className="grid gap-2 border-b border-line-1 p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 id="bible-h" className="text-sm font-semibold">
            Bible
          </h2>
          <SegmentedControl
            label="Lọc bible"
            value={filter}
            onChange={(v) => setFilter(v as 'all' | 'pending')}
            options={[
              { value: 'all', label: 'Tất cả' },
              { value: 'pending', label: `Chờ xác nhận ${nf.format(pending.length)}` },
            ]}
          />
        </div>
        <div className="grid grid-cols-[minmax(0,1fr)_auto] items-end gap-2 sm:grid-cols-[minmax(0,1fr)_10rem_auto]">
          <Input
            id="bible-q"
            type="search"
            label={<span className="sr-only">Tìm trong bible</span>}
            placeholder="Tìm trong bible — không cần dấu"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            trailing={<Search className="sr-ico text-fg-3" aria-hidden />}
          />
          <Select
            id="bible-type"
            label={<span className="sr-only">Loại</span>}
            value={type}
            onChange={setType}
            options={[{ value: '', label: 'Mọi loại' }, ...BIBLE_TYPES.map((t) => ({ value: t.value, label: t.label }))]}
            className="max-sm:hidden"
          />
          {viewer ? null : (
            <Button icon={Plus} onClick={() => setAdding(true)}>
              Thêm mục
            </Button>
          )}
        </div>
        {filter === 'pending' && pending.length > 1 && !viewer && !searching ? (
          <Button
            variant="ghost"
            icon={CheckCheck}
            className="justify-self-start text-xs"
            loading={setStatus.isPending}
            onClick={() => setStatus.mutate({ ids: pending.map((p) => p.id), action: 'confirm' })}
          >
            Xác nhận cả {nf.format(pending.length)} mục
          </Button>
        ) : null}
      </header>

      {list.isLoading ? (
        <div className="p-3">
          <SkeletonRows count={5} />
        </div>
      ) : list.error ? (
        <ErrorState className="p-3" what="Không tải được bible" error={list.error} onRetry={() => void list.refetch()} />
      ) : shown.length === 0 ? (
        <div className="p-4">
          <EmptyState
            icon={BookOpen}
            title={searching ? `Không có mục nào khớp “${dq}”.` : filter === 'pending' ? 'Không có mục nào chờ xác nhận.' : 'Dự án chưa có bible.'}
            description={
              searching
                ? 'Thử từ khác — tìm cả trong tóm tắt và bằng chứng trích dẫn.'
                : filter === 'pending'
                  ? 'Mục do LLM đề xuất sau mỗi tập sẽ chờ bạn ở đây.'
                  : 'Viết bible từ ý tưởng ở cột bên, hoặc thêm từng mục.'
            }
          />
        </div>
      ) : (
        <>
          {searching && search.data?.mode === 'or' ? (
            <p className="border-b border-line-1 px-3 py-1.5 text-xs text-fg-3">Không mục nào chứa đủ mọi từ — đang hiện mục chứa một trong các từ.</p>
          ) : null}
          <ul aria-label="Mục bible" aria-busy={search.isFetching || undefined}>
            {shown.map((it) => (
              <BibleItem
                key={it.id}
                item={it}
                evidence={evidenceOf(it)}
                expanded={open === it.id}
                onToggle={() => setOpen((o) => (o === it.id ? null : it.id))}
                viewer={viewer}
                busy={setStatus.isPending || lock.isPending}
                onConfirm={() => setStatus.mutate({ ids: [it.id], action: 'confirm' })}
                onReject={() => setStatus.mutate({ ids: [it.id], action: 'reject' })}
                onLock={(locked) => lock.mutate({ item: it, locked })}
                seqNumber={seqNumber}
              />
            ))}
          </ul>
        </>
      )}
      <AddBibleDialog open={adding} projectId={projectId} onClose={() => setAdding(false)} onDone={refresh} />
    </section>
  );
}

function AddBibleDialog({ open, projectId, onClose, onDone }: { open: boolean; projectId: number; onClose: () => void; onDone: () => void }) {
  const [type, setType] = useState<BibleType>('character');
  const [summary, setSummary] = useState('');
  const [quote, setQuote] = useState('');
  const [locked, setLocked] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (open) {
      setSummary('');
      setQuote('');
      setLocked(false);
      setErr(null);
    }
  }, [open]);
  const m = useMutation({
    mutationFn: () =>
      callOp('bible.create', {
        project_id: projectId,
        type,
        summary: summary.trim(),
        status: 'confirmed',
        locked,
        attributes: {},
        evidence: quote.trim() ? [{ quote: quote.trim(), source: 'manual' }] : [],
      } as never),
    onSuccess: () => {
      toast({ message: 'Đã thêm mục bible.', tone: 'success' });
      onDone();
      onClose();
    },
    onError: (e) => setErr(errorMessage(e, 'Không thêm được mục bible.')),
  });
  return (
    <Modal
      open={open}
      onClose={m.isPending ? undefined : onClose}
      title="Thêm mục bible"
      actions={
        <>
          <Button onClick={onClose} disabled={m.isPending}>
            Huỷ
          </Button>
          <Button variant="primary" icon={Plus} loading={m.isPending} disabled={!summary.trim()} onClick={() => m.mutate()}>
            Thêm mục
          </Button>
        </>
      }
    >
      <div className="grid gap-3">
        <Select id="bible-new-type" label="Loại" value={type} onChange={(v) => setType(v as BibleType)} options={BIBLE_TYPES} />
        <Textarea id="bible-new-summary" label="Tóm tắt" value={summary} onChange={(e) => setSummary(e.target.value)} minRows={2} maxRows={6} error={err ?? undefined} autoFocus />
        <Textarea
          id="bible-new-quote"
          label="Bằng chứng (tuỳ chọn)"
          hint="Câu trích nguyên văn từ kịch bản hoặc truyện."
          value={quote}
          onChange={(e) => setQuote(e.target.value)}
          minRows={2}
          maxRows={6}
        />
        <Checkbox label="Khoá — LLM không được sửa mục này (nhãn nhân thiết)" checked={locked} onChange={(e) => setLocked(e.target.checked)} />
      </div>
    </Modal>
  );
}
