import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useVirtualizer } from '@tanstack/react-virtual';
import { ArrowLeft, Check, ClipboardPaste, FileSpreadsheet, FolderOpen, Images, TriangleAlert, Upload } from 'lucide-react';
import { useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router';
import { callOp } from '../../api/ops';
import { callOpLoose } from '../../api/opsLoose';
import type { SequenceInfo, SequencePage } from '../../api/types';
import { errText } from '../../components/clip/useRegenerate';
import { Button } from '../../components/ui/Button';
import { Checkbox } from '../../components/ui/Checkbox';
import { ProgressBar } from '../../components/ui/ProgressBar';
import { SegmentedControl } from '../../components/ui/SegmentedControl';
import { cn } from '../../lib/cn';
import {
  buildEdits, EDIT_COLS, FIELD_LABEL, fileKind, guessPasteKind, isImageFile, rawOf, sortImageFiles,
  type ApplyResult, type PasteKind, type PreviewResult,
} from '../../lib/importPreview';
import { uploadFile } from '../../lib/importUpload';
import { toast } from '../../stores/toast';

type Source = 'paste' | 'file' | 'folder';
type Step = 'source' | 'preview' | 'done';

const PASTE_KINDS: { value: PasteKind | 'auto'; label: string }[] = [
  { value: 'auto', label: 'Tự nhận' },
  { value: 'lines', label: 'Mỗi dòng một clip' },
  { value: 'csv', label: 'CSV' },
  { value: 'json', label: 'JSON' },
  { value: 'yaml', label: 'YAML' },
];

const ROW_H = 64;
const COLS = '36px 44px 110px 100px minmax(200px,2fr) minmax(160px,1.3fr) minmax(160px,1.3fr) 72px 72px minmax(140px,1fr)';

/** `/p/:pid/import?seq=` — Nhập hàng loạt: dán / thả file / thư mục ảnh → bảng xem trước sửa được → áp dụng. */
export function ImportWizard() {
  const { projectId } = useParams();
  const pid = Number(projectId);
  const [sp] = useSearchParams();
  const navigate = useNavigate();
  const qc = useQueryClient();

  const seqs = useQuery({
    queryKey: ['shell', 'sequences', pid],
    queryFn: () => callOpLoose<SequencePage>('sequence.list', { project_id: pid, limit: 500 }),
    retry: false,
  });
  const [seqId, setSeqId] = useState<number | null>(() => {
    const s = sp.get('seq');
    return s && /^\d+$/.test(s) ? Number(s) : null;
  });
  const [newTitle, setNewTitle] = useState('');
  const [source, setSource] = useState<Source>('paste');
  const [text, setText] = useState('');
  const [pasteKind, setPasteKind] = useState<PasteKind | 'auto'>('auto');
  const [file, setFile] = useState<File | null>(null);
  const [images, setImages] = useState<File[]>([]);
  const [progress, setProgress] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [step, setStep] = useState<Step>('source');
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [lastSource, setLastSource] = useState<Record<string, unknown> | null>(null);
  const [drafts, setDrafts] = useState<Map<number, Record<string, string>>>(() => new Map());
  const [skip, setSkip] = useState<Set<number>>(() => new Set());
  const [createAssets, setCreateAssets] = useState(true);
  const [skipErrors, setSkipErrors] = useState(true);
  const [result, setResult] = useState<ApplyResult | null>(null);
  const [applyErrors, setApplyErrors] = useState<Map<number, string[]>>(() => new Map());
  const [dragOver, setDragOver] = useState(false);

  const targetSeq: SequenceInfo | undefined = seqs.data?.items.find((s) => s.id === seqId);

  const ensureSeq = async (): Promise<number> => {
    if (seqId != null) return seqId;
    const title = newTitle.trim() || 'Tập mới';
    const s = (await callOp('sequence.create', { project_id: pid, title, synopsis: '', script: '' })) as unknown as SequenceInfo;
    setSeqId(s.id);
    void qc.invalidateQueries({ queryKey: ['shell', 'sequences', pid] });
    return s.id;
  };

  const runPreview = async (mapping?: Record<string, string | null>) => {
    setBusy(true);
    setApplyErrors(new Map());
    try {
      const sid = await ensureSeq();
      let src = lastSource;
      if (!mapping || !src) {
        if (source === 'paste') {
          const kind = pasteKind === 'auto' ? guessPasteKind(text) : pasteKind;
          src = { kind, text };
        } else if (source === 'file') {
          if (!file) throw new Error('Chọn một file CSV/XLSX/JSON/YAML.');
          const kind = fileKind(file.name);
          if (!kind) throw new Error('File phải là CSV, XLSX, JSON hoặc YAML.');
          setProgress(0);
          const up = await uploadFile(file, { kind: 'other', projectId: pid, onProgress: setProgress });
          src = { kind, upload_id: up.uploadId };
        } else {
          if (!images.length) throw new Error('Chọn thư mục ảnh (mỗi ảnh một clip).');
          const ids: number[] = [];
          const sorted = sortImageFiles(images);
          for (let i = 0; i < sorted.length; i++) {
            const up = await uploadFile(sorted[i], {
              kind: 'image',
              projectId: pid,
              onProgress: (f) => setProgress((i + f) / sorted.length),
            });
            ids.push(up.media.id);
          }
          src = { kind: 'folder', media_ids: ids };
        }
        setLastSource(src);
      }
      const p = (await callOp('import.preview', { sequence_id: sid, source: src, mapping: mapping ?? null } as never)) as unknown as PreviewResult;
      setPreview(p);
      setDrafts(new Map());
      setSkip(new Set());
      setStep('preview');
    } catch (e) {
      toast({ message: `Không xem trước được: ${errText(e)}`, tone: 'error' });
    } finally {
      setBusy(false);
      setProgress(null);
    }
  };

  const apply = async () => {
    if (!preview || seqId == null) return;
    setBusy(true);
    try {
      const r = (await callOp('import.apply', {
        sequence_id: seqId,
        preview_token: preview.preview_token,
        edits: buildEdits(preview.rows, drafts),
        create_assets: createAssets,
        skip_rows: [...skip],
        skip_errors: skipErrors,
      } as never)) as unknown as ApplyResult;
      setResult(r);
      setStep('done');
      void qc.invalidateQueries({ queryKey: ['episode', seqId] });
      void qc.invalidateQueries({ queryKey: ['shell'] });
    } catch (e) {
      const extra = (e as { extra?: { rows?: { index: number; errors: string[] }[] } }).extra;
      if (extra?.rows?.length) setApplyErrors(new Map(extra.rows.map((r) => [r.index, r.errors])));
      toast({ message: `Không áp dụng được: ${errText(e)}`, tone: 'error' });
      if ((e as { code?: string }).code === 'preview_token_invalid') setStep('source');
    } finally {
      setBusy(false);
    }
  };

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const files = Array.from(e.dataTransfer.files);
    const imgs = files.filter(isImageFile);
    if (imgs.length && imgs.length === files.length) {
      setSource('folder');
      setImages(imgs);
    } else if (files[0]) {
      setSource('file');
      setFile(files[0]);
    } else {
      const t = e.dataTransfer.getData('text/plain');
      if (t) {
        setSource('paste');
        setText(t);
      }
    }
  };

  const canPreview = (source === 'paste' ? !!text.trim() : source === 'file' ? !!file : images.length > 0);

  return (
    <div className="grid min-w-0 gap-4" onDragOver={(e) => { e.preventDefault(); setDragOver(true); }} onDragLeave={() => setDragOver(false)} onDrop={onDrop}>
      <div className="flex min-w-0 flex-wrap items-end justify-between gap-3">
        <div className="grid min-w-0 gap-1">
          <h1 className="text-xl">Nhập hàng loạt</h1>
          <p className="max-w-prose text-sm text-fg-2">
            Dán kịch bản, thả file CSV/XLSX/JSON/YAML hoặc một thư mục ảnh — mỗi hàng (mỗi ảnh) thành một clip. Xem trước và sửa trước khi nhập; nhập xong hoàn tác được.
          </p>
        </div>
        <ol aria-label="Các bước" className="flex items-center gap-2 text-xs text-fg-2">
          {(['source', 'preview', 'done'] as Step[]).map((s, i) => (
            <li key={s} aria-current={step === s ? 'step' : undefined} className={cn('flex items-center gap-1.5', step === s && 'font-semibold text-fg-1')}>
              <span className={cn('grid h-5 w-5 place-items-center rounded-full border font-mono text-2xs', step === s ? 'border-accent text-accent' : 'border-line-2')}>{i + 1}</span>
              {['Nguồn', 'Xem trước', 'Xong'][i]}
            </li>
          ))}
        </ol>
      </div>

      {step === 'source' ? (
        <div className="grid min-w-0 gap-4 rounded-md border border-line-2 bg-bg-1 p-4 md:p-5">
          <div className="grid min-w-0 gap-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
            <label className="grid min-w-0 gap-1 text-sm font-medium">
              Tập nhận clip
              <select
                value={seqId ?? ''}
                onChange={(e) => setSeqId(e.target.value ? Number(e.target.value) : null)}
                className="h-control w-full rounded-sm border border-line-3 bg-bg-2 px-2 text-base font-normal md:text-sm"
              >
                <option value="">Tập mới…</option>
                {(seqs.data?.items ?? []).map((s) => (
                  <option key={s.id} value={s.id}>
                    Tập {s.number} · {s.title} ({(s.counts?.clips ?? 0).toLocaleString('vi-VN')} clip)
                  </option>
                ))}
              </select>
            </label>
            {seqId == null ? (
              <label className="grid min-w-0 gap-1 text-sm font-medium">
                Tên tập mới
                <input value={newTitle} onChange={(e) => setNewTitle(e.target.value)} placeholder="vd. Đêm trăng máu" className="h-control w-full rounded-sm border border-line-3 bg-bg-2 px-2 text-base font-normal md:text-sm" />
              </label>
            ) : (
              <p className="self-end text-xs text-fg-2">Clip mới nối vào cuối tập{targetSeq ? ` (đang có ${(targetSeq.counts?.clips ?? 0).toLocaleString('vi-VN')} clip)` : ''}.</p>
            )}
          </div>

          <SegmentedControl<Source>
            label="Nguồn nhập"
            options={[
              { value: 'paste', label: 'Dán chữ' },
              { value: 'file', label: 'File bảng' },
              { value: 'folder', label: 'Thư mục ảnh' },
            ]}
            value={source}
            onChange={setSource}
          />

          {source === 'paste' ? (
            <div className="grid min-w-0 gap-2">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <label htmlFor="imp-text" className="text-sm font-medium">Kịch bản hoặc bảng</label>
                <select
                  aria-label="Định dạng"
                  value={pasteKind}
                  onChange={(e) => setPasteKind(e.target.value as PasteKind | 'auto')}
                  className="h-control rounded-sm border border-line-3 bg-bg-2 px-2 text-sm"
                >
                  {PASTE_KINDS.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}
                </select>
              </div>
              <textarea
                id="imp-text"
                rows={10}
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder={'Mỗi dòng một clip, hoặc dán bảng có tiêu đề cột:\ncảnh,nhân vật,prompt ảnh,thoại,thời lượng\nS1 Miếu hoang,AN,An bước vào miếu,An: Ai đó?,6'}
                className="w-full rounded-sm border border-line-3 bg-bg-2 px-3 py-2 font-mono text-base leading-6 md:text-sm"
              />
              {text.trim() ? (
                <p className="text-xs text-fg-2">
                  Nhận dạng: {PASTE_KINDS.find((k) => k.value === (pasteKind === 'auto' ? guessPasteKind(text) : pasteKind))?.label}
                </p>
              ) : null}
            </div>
          ) : null}

          {source !== 'paste' ? (
            <label
              className={cn(
                'grid min-h-[160px] cursor-pointer place-content-center justify-items-center gap-2 rounded-md border border-dashed px-4 py-6 text-center',
                dragOver ? 'border-accent bg-bg-2' : 'border-line-3 hover:bg-bg-2',
                'focus-within:outline focus-within:outline-focus focus-within:outline-offset-focus focus-within:outline-accent',
              )}
            >
              {source === 'file' ? <FileSpreadsheet className="h-8 w-8 text-fg-3" aria-hidden /> : <Images className="h-8 w-8 text-fg-3" aria-hidden />}
              <span className="text-sm font-medium">
                {source === 'file'
                  ? file ? file.name : 'Thả file vào đây hoặc bấm để chọn'
                  : images.length ? `${images.length.toLocaleString('vi-VN')} ảnh đã chọn` : 'Thả ảnh / thư mục vào đây hoặc bấm để chọn thư mục'}
              </span>
              <span className="text-xs text-fg-2">
                {source === 'file' ? 'CSV, XLSX, JSON, YAML · tối đa 20 MB' : 'Mỗi ảnh một clip, theo thứ tự tên file (1, 2, 10…)'}
              </span>
              {source === 'file' ? (
                <input type="file" accept=".csv,.tsv,.txt,.xlsx,.xlsm,.json,.yaml,.yml" className="sr-only" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
              ) : (
                <input
                  type="file"
                  multiple
                  accept="image/*"
                  className="sr-only"
                  {...({ webkitdirectory: '' } as Record<string, string>)}
                  onChange={(e) => setImages(Array.from(e.target.files ?? []).filter(isImageFile))}
                />
              )}
            </label>
          ) : null}

          {progress != null ? (
            <div className="grid gap-1">
              <ProgressBar value={progress * 100} label="Đang tải lên" />
              <span className="text-xs text-fg-2 tnum">Đang tải lên {Math.round(progress * 100)}%</span>
            </div>
          ) : null}

          <div className="flex flex-wrap justify-end gap-2">
            {seqId != null ? (
              <Link
                to={`../s/${seqId}`}
                relative="path"
                className="inline-flex h-control items-center gap-2 rounded-md border border-transparent px-3 text-sm font-medium hover:bg-bg-3 focus-visible:outline focus-visible:outline-focus focus-visible:outline-accent"
              >
                <ArrowLeft className="sr-ico" aria-hidden /> Về bảng clip
              </Link>
            ) : null}
            <Button variant="primary" icon={source === 'paste' ? ClipboardPaste : source === 'file' ? Upload : FolderOpen} loading={busy} disabled={!canPreview} onClick={() => void runPreview()}>
              Xem trước
            </Button>
          </div>
        </div>
      ) : null}

      {step === 'preview' && preview ? (
        <PreviewStep
          preview={preview}
          drafts={drafts}
          setDrafts={setDrafts}
          skip={skip}
          setSkip={setSkip}
          applyErrors={applyErrors}
          createAssets={createAssets}
          setCreateAssets={setCreateAssets}
          skipErrors={skipErrors}
          setSkipErrors={setSkipErrors}
          busy={busy}
          onBack={() => setStep('source')}
          onRemap={(m) => void runPreview(m)}
          onApply={() => void apply()}
          seqLabel={targetSeq ? `Tập ${targetSeq.number} · ${targetSeq.title}` : 'tập mới'}
        />
      ) : null}

      {step === 'done' && result ? (
        <div className="grid gap-4 rounded-md border border-line-2 bg-bg-1 p-5">
          <h2 className="flex items-center gap-2 text-lg">
            <Check className="h-5 w-5 text-ok" aria-hidden /> Đã nhập {result.clip_ids.length.toLocaleString('vi-VN')} clip
          </h2>
          <ul className="grid gap-1 text-sm text-fg-2">
            <li>{result.scene_ids.length.toLocaleString('vi-VN')} cảnh · {result.dialogue_lines.toLocaleString('vi-VN')} câu thoại · {result.created_assets.length} tài sản mới</li>
            {result.skipped.length ? <li>Bỏ qua {result.skipped.length} hàng: {result.skipped.slice(0, 3).map((s) => `hàng ${s.index + 1} (${s.reason})`).join('; ')}</li> : null}
            {result.unresolved_assets.length ? <li className="text-warn">Chưa gắn được: {result.unresolved_assets.join(', ')}</li> : null}
          </ul>
          <div className="flex flex-wrap gap-2">
            <Button variant="primary" onClick={() => navigate(`../s/${seqId}`, { relative: 'path' })}>Mở bảng clip</Button>
            {result.change_set_id ? (
              <Button
                onClick={async () => {
                  try {
                    await callOp('history.undo', { change_set_id: result.change_set_id!, project_id: pid });
                    toast({ message: 'Đã hoàn tác lần nhập.' });
                    setStep('source');
                    void qc.invalidateQueries({ queryKey: ['episode', seqId] });
                  } catch (e) {
                    toast({ message: `Không hoàn tác được: ${errText(e)}`, tone: 'error' });
                  }
                }}
              >
                Hoàn tác lần nhập
              </Button>
            ) : null}
            <Button variant="ghost" onClick={() => { setStep('source'); setText(''); setFile(null); setImages([]); }}>Nhập tiếp</Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function PreviewStep(p: {
  preview: PreviewResult;
  drafts: Map<number, Record<string, string>>;
  setDrafts: (f: (m: Map<number, Record<string, string>>) => Map<number, Record<string, string>>) => void;
  skip: Set<number>;
  setSkip: (f: (s: Set<number>) => Set<number>) => void;
  applyErrors: Map<number, string[]>;
  createAssets: boolean;
  setCreateAssets: (v: boolean) => void;
  skipErrors: boolean;
  setSkipErrors: (v: boolean) => void;
  busy: boolean;
  onBack: () => void;
  onRemap: (m: Record<string, string | null>) => void;
  onApply: () => void;
  seqLabel: string;
}) {
  const { preview } = p;
  const scrollRef = useRef<HTMLDivElement>(null);
  const v = useVirtualizer({
    count: preview.rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_H,
    overscan: 8,
    paddingStart: 36,
    useFlushSync: false,
  });
  const [mapping, setMapping] = useState(preview.mapping);
  const mappingChanged = useMemo(() => JSON.stringify(mapping) !== JSON.stringify(preview.mapping), [mapping, preview.mapping]);
  const included = preview.rows.length - p.skip.size;
  const errRows = preview.rows.filter((r) => (r.errors.length || p.applyErrors.has(r.index)) && !p.skip.has(r.index)).length;

  const setCell = (index: number, key: string, value: string) =>
    p.setDrafts((m) => new Map(m).set(index, { ...(m.get(index) ?? {}), [key]: value }));

  return (
    <div className="grid min-w-0 gap-3">
      <div className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1 text-sm">
        <span className="font-semibold">{preview.summary.rows.toLocaleString('vi-VN')} hàng → {p.seqLabel}</span>
        {preview.summary.with_errors ? <span className="text-state-failed">{preview.summary.with_errors} hàng lỗi</span> : null}
        {preview.summary.with_warnings ? <span className="text-warn">{preview.summary.with_warnings} hàng cảnh báo</span> : null}
        <span className="text-xs text-fg-2">Bấm ô để sửa; ô đã sửa được kiểm lại khi nhập.</span>
      </div>
      {preview.warnings.map((w, i) => (
        <p key={i} className="sr-warnbox flex gap-2 rounded-sm px-3 py-2 text-sm"><TriangleAlert className="sr-ico mt-0.5 text-warn" aria-hidden />{w}</p>
      ))}

      {preview.columns.length ? (
        <details className="rounded-md border border-line-2 bg-bg-1 px-3 py-2 text-sm">
          <summary className="cursor-pointer font-medium">Ánh xạ cột ({preview.columns.length} cột)</summary>
          <div className="mt-2 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
            {preview.columns.map((c) => (
              <label key={c} className="grid min-w-0 gap-1 text-xs text-fg-2">
                <span className="truncate font-mono">{c}</span>
                <select
                  value={mapping[c] ?? ''}
                  onChange={(e) => setMapping((m) => ({ ...m, [c]: e.target.value || null }))}
                  className="h-control rounded-sm border border-line-3 bg-bg-2 px-2 text-sm text-fg-1"
                >
                  <option value="">Bỏ qua cột này</option>
                  {preview.fields.map((f) => <option key={f} value={f}>{FIELD_LABEL[f] ?? f}</option>)}
                </select>
              </label>
            ))}
          </div>
          {mappingChanged ? <Button className="mt-2" onClick={() => p.onRemap(mapping)} loading={p.busy}>Xem trước lại theo ánh xạ</Button> : null}
        </details>
      ) : null}

      <div ref={scrollRef} role="region" aria-label="Bảng xem trước" tabIndex={0} className="relative h-[min(60dvh,640px)] min-h-[300px] min-w-0 overflow-auto rounded-md border border-line-2 bg-bg-1">
        <div role="table" className="relative min-w-[1100px]" style={{ height: v.getTotalSize() }}>
          <div role="row" className="sticky top-0 z-sticky grid h-9 items-center whitespace-nowrap border-b border-line-2 bg-bg-1 text-xs text-fg-2" style={{ gridTemplateColumns: COLS }}>
            <span role="columnheader" className="px-2"><span className="sr-only">Nhập hàng này</span></span>
            <span role="columnheader" className="px-2">Hàng</span>
            {EDIT_COLS.map((c) => <span key={c.key} role="columnheader" className="px-2">{c.label}</span>)}
            <span role="columnheader" className="px-2">Kiểm tra</span>
          </div>
          {v.getVirtualItems().map((it) => {
            const r = preview.rows[it.index];
            const d = p.drafts.get(r.index) ?? {};
            const skipped = p.skip.has(r.index);
            const edited = Object.entries(d).some(([k, val]) => val !== rawOf(k, r.fields));
            const errs = p.applyErrors.get(r.index) ?? (edited ? [] : r.errors);
            return (
              <div
                key={r.index}
                role="row"
                className={cn('absolute inset-x-0 top-0 grid items-center border-b border-line-1 text-sm', skipped && 'opacity-50', errs.length && !skipped && 'bg-state-failed/5')}
                style={{ transform: `translateY(${it.start}px)`, height: ROW_H, gridTemplateColumns: COLS }}
              >
                <span role="cell" className="grid place-items-center">
                  <input
                    type="checkbox"
                    checked={!skipped}
                    aria-label={`Nhập hàng ${r.source_row}`}
                    onChange={(e) => p.setSkip((s) => { const n = new Set(s); if (e.target.checked) n.delete(r.index); else n.add(r.index); return n; })}
                    className="h-4 w-4 accent-[rgb(var(--c-accent))]"
                  />
                </span>
                <span role="cell" className="px-2 font-mono text-xs text-fg-2 tnum">{r.source_row}</span>
                {EDIT_COLS.map((c) => {
                  const val = d[c.key] ?? rawOf(c.key, r.fields);
                  const edited = d[c.key] !== undefined && d[c.key] !== rawOf(c.key, r.fields);
                  const cls = cn(
                    'h-[52px] w-full resize-none rounded-sm border bg-transparent px-1.5 py-1 text-sm leading-5 hover:border-line-2 focus:border-accent focus:bg-bg-2 focus:outline-none',
                    edited ? 'border-accent/60' : 'border-transparent',
                    c.mono && 'font-mono text-xs',
                  );
                  return (
                    <span key={c.key} role="cell" className="min-w-0 px-1">
                      <textarea aria-label={`${c.label} hàng ${r.source_row}`} value={val} rows={2} onChange={(e) => setCell(r.index, c.key, e.target.value)} className={cls} />
                    </span>
                  );
                })}
                <span role="cell" className="min-w-0 px-2 text-xs">
                  {errs.length ? (
                    <span className="line-clamp-3 text-state-failed" title={errs.join('\n')}>{errs.join(' ')}</span>
                  ) : edited ? (
                    <span className="text-fg-2">đã sửa — kiểm lại khi nhập</span>
                  ) : r.warnings.length ? (
                    <span className="line-clamp-3 text-warn" title={r.warnings.join('\n')}>{r.warnings.join(' ')}</span>
                  ) : (
                    <span className="flex items-center gap-1 text-fg-2"><Check className="h-3 w-3" aria-hidden />ổn</span>
                  )}
                </span>
              </div>
            );
          })}
        </div>
      </div>

      <div className="grid min-w-0 gap-2 rounded-md border border-line-2 bg-bg-1 p-3">
        {preview.assets_to_create.length ? (
          <Checkbox
            label={`Tạo ${preview.assets_to_create.length} nhân vật còn thiếu (${preview.assets_to_create.map((a) => a.code).join(', ')})`}
            checked={p.createAssets}
            onChange={(e) => p.setCreateAssets(e.target.checked)}
          />
        ) : null}
        <Checkbox label="Bỏ qua hàng còn lỗi (không thì từ chối cả lô)" checked={p.skipErrors} onChange={(e) => p.setSkipErrors(e.target.checked)} />
        <div className="flex flex-wrap items-center justify-end gap-2">
          <Button variant="ghost" icon={ArrowLeft} onClick={p.onBack}>Đổi nguồn</Button>
          <Button variant="primary" icon={Check} loading={p.busy} disabled={included === 0 || (!p.skipErrors && errRows > 0)} onClick={p.onApply}>
            Nhập {Math.max(0, included - (p.skipErrors ? errRows : 0)).toLocaleString('vi-VN')} clip · miễn phí
          </Button>
        </div>
      </div>
    </div>
  );
}
