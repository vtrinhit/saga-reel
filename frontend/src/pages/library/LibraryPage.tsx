import { AudioLines, Library, Plus, ScanSearch, Search, X } from 'lucide-react';
import { useMemo, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router';
import { callOp } from '../../api/ops';
import { AssetCard, SuggestedAssetCard } from '../../components/library/AssetCard';
import { AssetCreateDialog } from '../../components/library/AssetCreateDialog';
import { AssetDetail } from '../../components/library/AssetDetail';
import { DescribeDialog } from '../../components/library/DescribeDialog';
import {
  bibleName,
  pidOf,
  toastError,
  useAssetKinds,
  useAssets,
  useBibleSuggestions,
  useLibraryLiveRefresh,
  useVoices,
} from '../../components/library/useLibrary';
import { useMediaQuery, XL } from '../../components/library/useMediaQuery';
import { Button } from '../../components/ui/Button';
import { EmptyState } from '../../components/ui/EmptyState';
import { ErrorState } from '../../components/ui/ErrorState';
import { SegmentedControl } from '../../components/ui/SegmentedControl';
import { cn } from '../../lib/cn';
import { foldVi } from '../../lib/format';
import { addLabel, countByKind, kindLabel, matchAsset, sortKinds, type Asset, type BibleItem } from '../../lib/library';
import { toast } from '../../stores/toast';
import { PageHeader } from '../PageHeader';

const VOICE_TAB = 'voice';
const GRID = 'grid grid-cols-2 gap-2 sm:grid-cols-[repeat(auto-fill,minmax(176px,1fr))] sm:gap-3';

/** Ô tìm (gõ không dấu vẫn khớp), có nút xoá. */
function SearchBox({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <div className="relative w-full min-w-0 sm:w-64">
      <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-3" strokeWidth={1.75} aria-hidden />
      <input
        type="search"
        aria-label="Tìm tài sản theo tên, mã hoặc look"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Tìm theo tên hoặc mã"
        className="min-h-[var(--h-control)] w-full rounded-sm border border-line-3 bg-bg-2 py-1 pl-8 pr-8 text-md placeholder:text-fg-3 hover:border-fg-3 focus-visible:outline focus-visible:outline-focus focus-visible:outline-offset-focus focus-visible:outline-accent md:text-base [&::-webkit-search-cancel-button]:hidden"
      />
      {value ? (
        <button
          type="button"
          onClick={() => onChange('')}
          className="sr-hit absolute right-1 top-1/2 grid h-7 w-7 -translate-y-1/2 place-items-center rounded-sm text-fg-2 hover:text-fg-1"
          aria-label="Xoá ô tìm"
        >
          <X className="h-3.5 w-3.5" strokeWidth={2} aria-hidden />
        </button>
      ) : null}
    </div>
  );
}

/**
 * `/p/:pid/library` — tab theo loại tài sản (+ Giọng) · tìm · lưới AssetCard; ≥1280 inspector 360 bên phải
 * (`?asset=`), dưới 1280 bấm thẻ mở trang `library/:assetId`. Tài sản bible gợi ý hiện thẻ "chờ xác nhận".
 */
export function LibraryPage() {
  const { projectId } = useParams();
  const pid = pidOf(projectId);
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const wide = useMediaQuery(XL);
  useLibraryLiveRefresh();
  const kinds = useAssetKinds(pid);
  const assets = useAssets(pid);
  const voices = useVoices(pid);
  const bible = useBibleSuggestions(pid);
  const [q, setQ] = useState('');
  const [creating, setCreating] = useState<{ name?: string; bible?: BibleItem } | null>(null);
  const [describing, setDescribing] = useState(false);

  const kindList = useMemo(() => sortKinds(kinds.data ?? []), [kinds.data]);
  const counts = useMemo(() => countByKind(assets.data ?? []), [assets.data]);
  const selectedId = params.get('asset') ? Number(params.get('asset')) : null;
  const tab =
    params.get('kind') ??
    (assets.data ?? []).find((a) => a.id === selectedId)?.kind ??
    kindList[0]?.key ??
    'character';
  const voiceName = useMemo(() => {
    const m = new Map((voices.data ?? []).map((v) => [v.id, v.name]));
    return (a: Asset) => (a.voice_profile_id != null ? (m.get(a.voice_profile_id) ?? null) : null);
  }, [voices.data]);

  const shown = useMemo(
    () => (assets.data ?? []).filter((a) => (q.trim() ? true : a.kind === tab) && matchAsset(a, q)),
    [assets.data, tab, q],
  );
  const suggestions = (bible.data ?? []).filter(
    (b) => (q.trim() ? foldVi(bibleName(b)).includes(foldVi(q.trim())) : b.type === tab),
  );
  const selected = wide && selectedId != null ? (assets.data ?? []).find((a) => a.id === selectedId) : undefined;
  const isVoiceTab = tab === VOICE_TAB;

  const setTab = (k: string) => {
    const next = new URLSearchParams(params);
    next.set('kind', k);
    next.delete('asset');
    setParams(next, { replace: true });
  };
  const openAsset = (a: Asset): boolean => {
    if (!wide) return false;
    const next = new URLSearchParams(params);
    next.set('asset', String(a.id));
    setParams(next, { replace: true });
    return true;
  };

  const confirmSuggestion = async (b: BibleItem, created: Asset) => {
    try {
      await callOp('bible.update', { id: b.id, values: { asset_id: created.id } });
      if (b.status === 'pending') await callOp('bible.confirm', { ids: [b.id] });
    } catch (e) {
      toastError(e, 'Đã tạo tài sản nhưng chưa xác nhận được mục bible.');
    }
  };

  if (pid == null) return null;
  if (assets.isError || kinds.isError) {
    return (
      <>
        <PageHeader title="Thư viện" />
        <ErrorState
          what="Không tải được thư viện"
          error={assets.error ?? kinds.error}
          onRetry={() => {
            void assets.refetch();
            void kinds.refetch();
          }}
        />
      </>
    );
  }

  const tabOptions = [
    ...kindList.map((k) => ({ value: k.key, label: `${k.label} ${counts[k.key] ?? 0}` })),
    ...(voices.isError ? [] : [{ value: VOICE_TAB, label: `Giọng ${voices.data?.length ?? 0}` }]),
  ];

  return (
    <>
      <PageHeader
        title="Thư viện"
        actions={
          <>
            <Button icon={ScanSearch} onClick={() => setDescribing(true)} disabled={!kindList.length}>
              Mô tả từ ảnh
            </Button>
            {!isVoiceTab ? (
              <Button variant="primary" icon={Plus} onClick={() => setCreating({})} disabled={!kindList.length}>
                {addLabel(kindList, tab)}
              </Button>
            ) : null}
          </>
        }
      />

      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        {kindList.length ? (
          <SegmentedControl label="Loại tài sản" options={tabOptions} value={tab} onChange={setTab} className="max-w-full" />
        ) : (
          <div className="h-control w-72 rounded-md bg-bg-2" aria-hidden />
        )}
        {!isVoiceTab ? <SearchBox value={q} onChange={setQ} /> : null}
      </div>

      <div className={cn('grid gap-4', selected && 'xl:grid-cols-[minmax(0,1fr)_var(--w-inspector)]')}>
        <section aria-label={isVoiceTab ? 'Giọng' : kindLabel(kindList, tab)} className="min-w-0">
          {isVoiceTab ? (
            <VoiceList pid={pid} />
          ) : assets.isPending ? (
            <ul className={GRID} aria-busy="true" aria-label="Đang tải thư viện">
              {Array.from({ length: 8 }, (_, i) => (
                <li key={i} className="grid overflow-hidden rounded-md border border-line-1 bg-bg-2">
                  <div className="aspect-square bg-bg-1" />
                  <div className="grid gap-2 p-2.5">
                    <span className="h-2.5 w-3/5 rounded-xs bg-bg-3" />
                    <span className="h-2.5 w-2/5 rounded-xs bg-bg-3" />
                  </div>
                </li>
              ))}
            </ul>
          ) : shown.length === 0 && suggestions.length === 0 ? (
            <div className="rounded-md border border-line-2 bg-bg-1 p-6 md:p-8">
              <EmptyState
                icon={Library}
                title={
                  q.trim()
                    ? `Không có tài sản nào khớp "${q.trim()}".`
                    : `Dự án chưa có ${kindLabel(kindList, tab).toLowerCase()} nào.`
                }
                description={
                  q.trim()
                    ? 'Tìm theo tên, mã hoặc tên look; gõ không dấu cũng được.'
                    : 'Thêm bằng tay, hoặc tải một ảnh tham chiếu để AI viết mô tả khoá.'
                }
                action={
                  q.trim() ? (
                    <Button onClick={() => setQ('')}>Xoá ô tìm</Button>
                  ) : (
                    <Button variant="primary" icon={Plus} onClick={() => setCreating({})}>
                      {addLabel(kindList, tab)}
                    </Button>
                  )
                }
              />
            </div>
          ) : (
            <ul className={GRID}>
              {shown.map((a) => (
                <li key={a.id} className="min-w-0">
                  <AssetCard
                    asset={a}
                    href={`/p/${pid}/library/${a.id}`}
                    selected={selected?.id === a.id}
                    voiceName={voiceName(a)}
                    onOpen={() => openAsset(a)}
                  />
                </li>
              ))}
              {suggestions.map((b) => (
                <li key={`b${b.id}`} className="min-w-0">
                  <SuggestedAssetCard
                    name={bibleName(b)}
                    code={bibleName(b).split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join('').toUpperCase()}
                    note={b.summary || 'gợi ý từ bible · chưa có sheet'}
                    pending={b.status === 'pending'}
                    action={
                      <Button className="mt-1" onClick={() => setCreating({ name: bibleName(b), bible: b })}>
                        Tạo tài sản
                      </Button>
                    }
                  />
                </li>
              ))}
            </ul>
          )}
        </section>

        {selected ? (
          <aside
            aria-label={`Chi tiết ${selected.name}`}
            className="relative hidden min-w-0 self-start overflow-y-auto overscroll-contain rounded-md border border-line-2 bg-bg-1 p-4 xl:sticky xl:top-[calc(var(--sr-top)+16px)] xl:block xl:max-h-[calc(100dvh-var(--sr-top)-32px)]"
          >
            <div className="absolute right-2 top-2 z-[1]">
              <button
                type="button"
                className="sr-hit grid h-7 w-7 place-items-center rounded-sm text-fg-2 hover:bg-bg-3 hover:text-fg-1"
                aria-label="Đóng chi tiết"
                onClick={() => {
                  const next = new URLSearchParams(params);
                  next.delete('asset');
                  setParams(next, { replace: true });
                }}
              >
                <X className="h-4 w-4" strokeWidth={1.75} aria-hidden />
              </button>
            </div>
            <AssetDetail key={selected.id} asset={selected} kinds={kindList} projectId={pid} layout="inspector" />
          </aside>
        ) : null}
      </div>

      {creating ? (
        <AssetCreateDialog
          projectId={pid}
          kinds={kindList}
          defaultKind={creating.bible?.type ?? (isVoiceTab ? (kindList[0]?.key ?? 'character') : tab)}
          initial={{ name: creating.name }}
          onClose={() => setCreating(null)}
          onCreated={async (a) => {
            if (creating.bible) await confirmSuggestion(creating.bible, a);
            setCreating(null);
            if (wide) openAsset(a);
            else navigate(`/p/${pid}/library/${a.id}`);
          }}
        />
      ) : null}
      {describing ? (
        <DescribeDialog
          projectId={pid}
          kinds={kindList}
          defaultKind={isVoiceTab ? (kindList[0]?.key ?? 'character') : tab}
          onClose={() => setDescribing(false)}
          onCreated={(a) => {
            if (wide) openAsset(a);
            else toast({ message: `Mở ${a.name} để tạo sheet.`, tone: 'info', action: { label: 'Mở', onClick: () => navigate(`/p/${pid}/library/${a.id}`) } });
          }}
        />
      ) : null}
    </>
  );
}

/** Tab Giọng: hồ sơ giọng của dự án + toàn cục (chỉ xem; sửa/nghe thử ở màn Âm thanh). */
function VoiceList({ pid }: { pid: number }) {
  const voices = useVoices(pid);
  if (voices.isPending) return <p className="text-sm text-fg-2">Đang tải giọng…</p>;
  const list = voices.data ?? [];
  if (!list.length) {
    return (
      <div className="rounded-md border border-line-2 bg-bg-1 p-6 md:p-8">
        <EmptyState
          icon={AudioLines}
          title="Chưa có hồ sơ giọng."
          description="Hồ sơ giọng (TTS, tốc độ, cao độ) được tạo ở màn Âm thanh của tập, rồi gán cho nhân vật tại đây."
        />
      </div>
    );
  }
  return (
    <ul className="divide-y divide-line-1 rounded-md border border-line-2 bg-bg-1">
      {list.map((v) => (
        <li key={v.id} className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-0.5 px-3 py-2.5">
          <AudioLines className="sr-ico text-fg-3" aria-hidden />
          <span className="min-w-0 truncate text-sm font-medium">{v.name}</span>
          <span className="min-w-0 truncate font-mono text-xs text-fg-2">{v.voice || 'giọng mặc định'}</span>
          <span className="ml-auto text-xs text-fg-3">{v.project_id == null ? 'toàn cục' : 'dự án'}</span>
        </li>
      ))}
    </ul>
  );
}
