import { ImageUp, ScanSearch, Upload } from 'lucide-react';
import { useEffect, useRef, useState, type DragEvent } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useJob } from '../../api/hooks';
import { callOp } from '../../api/ops';
import { cn, focusRing } from '../../lib/cn';
import {
  defaultLook,
  kindLabel,
  parseDescribeResult,
  type Asset,
  type AssetKind,
  type DescribeSuggestion,
} from '../../lib/library';
import { uploadFile } from '../../lib/libraryUpload';
import { toast } from '../../stores/toast';
import { Button } from '../ui/Button';
import { Input } from '../ui/Input';
import { Modal } from '../ui/Modal';
import { ProgressBar } from '../ui/ProgressBar';
import { Select } from '../ui/Select';
import { errText, libKeys, toastError } from './useLibrary';

const MAX_IMAGE = 50 * 1024 * 1024;
const DONE = new Set(['succeeded', 'failed', 'cancelled', 'needs_attention', 'interrupted']);

type Phase = 'pick' | 'uploading' | 'describing' | 'ready';

/**
 * Mô tả từ ảnh: chọn/thả một ảnh → tải lên chia khối (`upload.*`) → `asset.describe_from_image` (job LLM vision) →
 * gợi ý mô tả khoá, cụm khoá, nét phải giữ. Không tự lưu: người dùng bấm "Tạo …" hoặc "Dùng gợi ý" cho tài sản đang mở.
 */
export function DescribeDialog({
  projectId,
  kinds,
  defaultKind,
  asset,
  onClose,
  onCreated,
}: {
  projectId: number;
  kinds: AssetKind[];
  defaultKind: string;
  /** Có: áp gợi ý vào tài sản này thay vì tạo mới. */
  asset?: Asset;
  onClose: () => void;
  onCreated?: (a: Asset) => void;
}) {
  const qc = useQueryClient();
  const [kind, setKind] = useState(asset?.kind ?? defaultKind);
  const [hint, setHint] = useState(asset?.name ?? '');
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [phase, setPhase] = useState<Phase>('pick');
  const [progress, setProgress] = useState(0);
  const [mediaId, setMediaId] = useState<number | null>(null);
  const [jobId, setJobId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [suggest, setSuggest] = useState<DescribeSuggestion | null>(null);
  const [saving, setSaving] = useState(false);
  const [drag, setDrag] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const abort = useRef<AbortController | null>(null);
  const job = useJob(jobId);

  useEffect(() => () => abort.current?.abort(), []);
  useEffect(() => {
    if (!file) return;
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  useEffect(() => {
    const j = job.data;
    if (!j || phase !== 'describing' || !DONE.has(String(j.status))) return;
    if (j.status === 'succeeded') {
      const s = parseDescribeResult(j.result);
      if (s) {
        setSuggest(s);
        setPhase('ready');
      } else {
        setError('LLM trả kết quả không đọc được. Thử lại, hoặc đổi mô hình cho tác vụ "Mô tả tài sản từ ảnh" trong Cài đặt › Định tuyến LLM.');
        setPhase('pick');
      }
    } else {
      setError(`${j.error_message ?? 'Việc mô tả ảnh không xong.'}${j.hint ? ` ${j.hint}` : ''}`);
      setPhase('pick');
    }
  }, [job.data, phase]);

  const pick = (f: File | undefined | null) => {
    setError(null);
    if (!f) return;
    if (!f.type.startsWith('image/')) {
      setError('Chọn một file ảnh (JPG, PNG, WebP).');
      return;
    }
    if (f.size > MAX_IMAGE) {
      setError('Ảnh lớn hơn 50 MB — thu nhỏ ảnh rồi thử lại.');
      return;
    }
    setFile(f);
    setMediaId(null);
    setSuggest(null);
  };

  const run = async () => {
    if (!file) return;
    setError(null);
    try {
      let mid = mediaId;
      if (mid == null) {
        setPhase('uploading');
        abort.current = new AbortController();
        const m = await uploadFile(file, {
          kind: 'image',
          projectId,
          onProgress: setProgress,
          signal: abort.current.signal,
        });
        mid = m.id;
        setMediaId(mid);
      }
      setPhase('describing');
      const r = (await callOp('asset.describe_from_image', {
        media_id: mid,
        kind,
        hint: hint.trim(),
        project_id: projectId,
      })) as unknown as { job_id: number };
      setJobId(r.job_id);
    } catch (e) {
      if ((e as Error)?.name === 'AbortError' || (e as { code?: string })?.code === 'ERR_CANCELED') return;
      setError(errText(e, 'Không tải được ảnh lên.'));
      setPhase('pick');
    }
  };

  const applyToLook = async (a: Asset, s: DescribeSuggestion, mid: number | null) => {
    const lk = defaultLook(a);
    if (!lk) return;
    const values: Record<string, unknown> = {};
    if (s.lock_word) values.lock_word = s.lock_word;
    if (s.keep_features.length) values.keep_features = s.keep_features;
    if (Object.keys(values).length) await callOp('look.update', { id: lk.id, values, expected_updated_at: lk.updated_at });
    if (mid != null) await callOp('look.add_refs', { look_id: lk.id, media_ids: [mid] });
  };

  const save = async () => {
    if (!suggest) return;
    setSaving(true);
    try {
      if (asset) {
        const values: Record<string, unknown> = {};
        if (!asset.locked && suggest.description) values.description = suggest.description;
        if (suggest.tags.length) values.tags = [...new Set([...(asset.tags ?? []), ...suggest.tags])];
        if (Object.keys(values).length) {
          await callOp('asset.update', { id: asset.id, values, expected_updated_at: asset.updated_at });
        }
        await applyToLook(asset, suggest, mediaId);
        toast({
          message: asset.locked
            ? `Đã cập nhật cụm khoá của ${asset.name} (mô tả đang khoá nên giữ nguyên).`
            : `Đã dùng gợi ý cho ${asset.name}.`,
          tone: 'success',
        });
      } else {
        const created = (await callOp('asset.create', {
          project_id: projectId,
          kind,
          name: hint.trim() || suggest.name || 'Chưa đặt tên',
          description: suggest.description,
          tags: suggest.tags,
        })) as unknown as Asset;
        await applyToLook(created, suggest, mediaId);
        toast({ message: `Đã thêm ${created.name} vào thư viện.`, tone: 'success' });
        onCreated?.(created);
      }
      void qc.invalidateQueries({ queryKey: libKeys.all });
      onClose();
    } catch (e) {
      toastError(e, 'Không lưu được gợi ý.');
    } finally {
      setSaving(false);
    }
  };

  const busy = phase === 'uploading' || phase === 'describing';
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDrag(false);
    if (!busy) pick(e.dataTransfer.files?.[0]);
  };
  const set = <K extends keyof DescribeSuggestion>(k: K, v: DescribeSuggestion[K]) => setSuggest((s) => (s ? { ...s, [k]: v } : s));

  return (
    <Modal
      open
      size="lg"
      title={asset ? `Mô tả lại ${asset.name} từ ảnh` : 'Mô tả từ ảnh'}
      onClose={busy || saving ? undefined : onClose}
      actions={
        <>
          <Button
            onClick={() => {
              abort.current?.abort();
              onClose();
            }}
            disabled={saving}
          >
            Huỷ
          </Button>
          {phase === 'ready' && suggest ? (
            <Button variant="primary" onClick={() => void save()} loading={saving}>
              {asset ? 'Dùng gợi ý' : `Thêm ${kindLabel(kinds, kind).toLowerCase()}`}
            </Button>
          ) : (
            <Button variant="primary" icon={ScanSearch} onClick={() => void run()} loading={busy} disabled={!file || busy}>
              {phase === 'uploading' ? 'Đang tải ảnh lên' : phase === 'describing' ? 'Đang mô tả' : 'Mô tả ảnh · 1 lượt LLM'}
            </Button>
          )}
        </>
      }
    >
      <div className="grid gap-4 md:grid-cols-[200px_minmax(0,1fr)]">
        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDrag(true);
          }}
          onDragLeave={() => setDrag(false)}
          onDrop={onDrop}
          className={cn(
            'relative grid aspect-[3/4] w-full max-w-[240px] place-items-center overflow-hidden rounded-md border border-dashed bg-bg-1',
            drag ? 'border-accent' : 'border-line-2',
          )}
        >
          {preview ? <img src={preview} alt="Ảnh đã chọn" className="absolute inset-0 h-full w-full object-contain" /> : null}
          <button
            type="button"
            disabled={busy}
            onClick={() => inputRef.current?.click()}
            className={cn(
              'relative z-[1] inline-flex min-h-touch items-center gap-2 rounded-md border border-line-2 bg-bg-2 px-3 text-sm',
              preview && 'sr-pill-on-media mt-auto mb-2',
              focusRing,
            )}
          >
            {preview ? <ImageUp className="sr-ico" aria-hidden /> : <Upload className="sr-ico" aria-hidden />}
            {preview ? 'Đổi ảnh' : 'Chọn hoặc thả ảnh'}
          </button>
          <input
            ref={inputRef}
            type="file"
            accept="image/*"
            className="sr-only"
            tabIndex={-1}
            aria-hidden
            onChange={(e) => pick(e.target.files?.[0])}
          />
        </div>

        <div className="grid min-w-0 content-start gap-3">
          {!asset ? (
            <Select
              id="describe-kind"
              label="Loại"
              value={kind}
              options={kinds.map((k) => ({ value: k.key, label: k.label }))}
              onChange={setKind}
              disabled={busy}
            />
          ) : null}
          <Input
            id="describe-hint"
            label="Tên hoặc ghi chú"
            value={hint}
            disabled={busy}
            onChange={(e) => setHint(e.target.value)}
            placeholder="vd. Lạc An, hồ ly 17 tuổi"
            hint="Gửi kèm cho LLM để mô tả đúng nhân vật."
          />
          {phase === 'uploading' ? (
            <div className="grid gap-1">
              <span className="text-sm text-fg-2">
                Đang tải ảnh lên · <span className="font-mono tnum">{Math.round(progress * 100)}%</span>
              </span>
              <ProgressBar value={progress * 100} label="Tiến độ tải ảnh lên" />
            </div>
          ) : null}
          {phase === 'describing' ? (
            <p className="text-sm text-fg-2" aria-live="polite">
              LLM đang xem ảnh{job.data?.status === 'queued' ? ' (đang chờ lượt)' : ''}…
            </p>
          ) : null}
          {error ? (
            <p role="alert" className="text-sm text-state-failed">
              {error}
            </p>
          ) : null}
          {phase === 'ready' && suggest ? (
            <section aria-label="Gợi ý" className="grid gap-3 border-t border-line-1 pt-3">
              <label className="grid gap-1 text-sm">
                <span className="font-medium">Mô tả khoá</span>
                <textarea
                  value={suggest.description}
                  onChange={(e) => set('description', e.target.value)}
                  rows={5}
                  className="w-full min-w-0 rounded-sm border border-line-3 bg-bg-2 px-2.5 py-1.5 text-sm"
                />
                {asset?.locked ? <span className="text-xs text-fg-3">Mô tả của {asset.name} đang khoá — phần này không được ghi.</span> : null}
              </label>
              <Input
                id="describe-lock"
                label="Cụm khoá"
                value={suggest.lock_word}
                onChange={(e) => set('lock_word', e.target.value)}
                className="font-mono text-sm md:text-sm"
              />
              {suggest.keep_features.length ? (
                <div className="grid gap-1">
                  <span className="text-sm font-medium">Phải giữ</span>
                  <ul className="flex flex-wrap gap-1">
                    {suggest.keep_features.map((f) => (
                      <li key={f} className="rounded-xs bg-bg-3 px-1.5 text-xs">
                        {f}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
              <p className="text-xs text-fg-3">Ảnh gốc được thêm làm ảnh tham chiếu của look mặc định. Mỏ neo vẫn tạo từ chữ.</p>
            </section>
          ) : null}
        </div>
      </div>
    </Modal>
  );
}
