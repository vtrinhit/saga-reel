import { useQuery } from '@tanstack/react-query';
import { CircleAlert, FlaskConical, Play } from 'lucide-react';
import { useReducer, useState } from 'react';
import { useNavigate } from 'react-router';
import { toApiError } from '../../api/client';
import { qk } from '../../api/hooks';
import { callOp } from '../../api/ops';
import type { Adapter, Connection, MediaItem, TryEstimate } from '../../api/types';
import { Button } from '../../components/ui/Button';
import { EstimateDialog } from '../../components/ui/EstimateDialog';
import { Input } from '../../components/ui/Input';
import { Modal } from '../../components/ui/Modal';
import { Select } from '../../components/ui/Select';
import { Textarea } from '../../components/ui/Textarea';
import { formatCredits } from '../../lib/format';
import { estimateRows, INITIAL, supportsDryRun, tryReducer } from '../../lib/tryFlow';
import { useConnection } from '../../stores/connection';
import { JobWatch } from '../jobs/JobWatch';
import { KIND_LABEL } from './providerText';

interface TryForm {
  prompt: string;
  text: string;
  voice: string;
  model: string;
  aspect: string;
  duration: string;
  mediaId: string;
}

const EMPTY: TryForm = { prompt: '', text: '', voice: '', model: '', aspect: '', duration: '', mediaId: '' };

function caps(adapter: Adapter | undefined, conn: Connection): Record<string, unknown> {
  const c: Record<string, unknown> = { ...(adapter?.default_capabilities ?? {}) };
  for (const k of Object.keys(c)) if (k in conn.options) c[k] = conn.options[k];
  return c;
}

const list = (v: unknown): string[] => (Array.isArray(v) ? v.map(String) : []);

/** Params của `provider.try_<kind>`; luôn truyền `connection` rõ ràng (không dựa vào kết nối mặc định). */
/** Gọi op `provider.try_<kind>` đúng kiểu của từng loại kết nối (params lấy từ `schema.d.ts`). */
function callTry(conn: Connection, f: TryForm, dryRun: boolean): Promise<unknown> {
  const connection = conn.id;
  switch (conn.kind) {
    case 'image':
      return callOp('provider.try_image', { connection, prompt: f.prompt, aspect: f.aspect || undefined, dry_run: dryRun });
    case 'video':
      return callOp('provider.try_video', {
        connection,
        prompt: f.prompt,
        image_media_id: Number(f.mediaId),
        duration: f.duration ? Number(f.duration) : undefined,
        dry_run: dryRun,
      });
    case 'tts':
      return callOp('provider.try_tts', { connection, text: f.text, voice: f.voice.trim() || undefined, dry_run: dryRun });
    case 'llm':
      return callOp('provider.try_llm', { connection, prompt: f.prompt, model: f.model.trim() || undefined });
    case 'stt':
      return callOp('provider.try_stt', { connection, audio_media_id: Number(f.mediaId) });
  }
}

function formReady(conn: Connection, f: TryForm): boolean {
  switch (conn.kind) {
    case 'image':
    case 'llm':
      return f.prompt.length > 0;
    case 'video':
      return f.prompt.length > 0 && !!f.mediaId;
    case 'tts':
      return f.text.length > 0;
    case 'stt':
      return !!f.mediaId;
  }
}

/** Chọn media có sẵn trong kho (ảnh cho video, âm thanh cho STT). */
function MediaPicker({ kind, value, onChange }: { kind: 'image' | 'audio'; value: string; onChange: (v: string) => void }) {
  const q = useQuery({
    queryKey: qk.media(kind),
    queryFn: async () => (await callOp('media.list', { kind, limit: 100 })).items as unknown as MediaItem[],
  });
  const items = q.data ?? [];
  const label = kind === 'image' ? 'Ảnh đầu vào' : 'Tệp âm thanh';
  return (
    <Select
      id={`try-media-${kind}`}
      label={label}
      value={value || null}
      onChange={onChange}
      placeholder={q.isLoading ? 'Đang tải…' : items.length ? 'Chọn…' : 'Kho media chưa có tệp nào'}
      disabled={!items.length}
      hint={
        !q.isLoading && !items.length
          ? kind === 'image'
            ? 'Thử một kết nối tạo ảnh trước — ảnh tạo ra sẽ có ở đây.'
            : 'Thử một kết nối giọng đọc (TTS) trước — âm thanh tạo ra sẽ có ở đây.'
          : undefined
      }
      options={items.map((m) => ({
        value: String(m.id),
        label: `${kind === 'image' ? 'Ảnh' : 'Âm thanh'} #${m.id}`,
        description:
          kind === 'image'
            ? `${m.width ?? '?'}×${m.height ?? '?'}`
            : m.duration_ms != null
              ? `${(m.duration_ms / 1000).toLocaleString('vi-VN', { maximumFractionDigits: 1 })} s`
              : undefined,
      }))}
    />
  );
}

export interface TryDialogProps {
  conn: Connection;
  adapter?: Adapter;
  onClose: () => void;
}

/**
 * "Thử" một kết nối: form theo loại → ước tính (`dry_run`, chỉ ảnh/video/TTS) → EstimateDialog → chạy → theo dõi job
 * sống ngay trong hộp. LLM/STT chưa có `dry_run` → bước xác nhận nói rõ chưa ước tính được.
 */
export function TryDialog({ conn, adapter, onClose }: TryDialogProps) {
  const navigate = useNavigate();
  const [state, dispatch] = useReducer(tryReducer, INITIAL);
  const [form, setForm] = useState<TryForm>(EMPTY);
  const offline = useConnection((s) => s.state === 'offline');
  const c = caps(adapter, conn);
  const dryRun = supportsDryRun(conn.kind);
  const set = (k: keyof TryForm, v: string) => setForm((f) => ({ ...f, [k]: v }));

  // Gọi op từ handler (không từ effect): việc trả phí không bao giờ bị gửi hai lần vì effect chạy lại.
  const estimate = () => {
    if (!formReady(conn, form) || state.step !== 'form') return;
    if (!dryRun) {
      dispatch({ type: 'submit', dryRun: false });
      return;
    }
    dispatch({ type: 'submit', dryRun: true });
    callTry(conn, form, true)
      .then((r) => dispatch({ type: 'estimated', estimate: (r as { estimate: TryEstimate }).estimate }))
      .catch((e) => dispatch({ type: 'failed', error: toApiError(e) }));
  };
  const runNow = () => {
    if (state.step !== 'estimate' && state.step !== 'confirm') return;
    dispatch({ type: 'run' });
    callTry(conn, form, false)
      .then((r) => dispatch({ type: 'started', jobId: (r as { job_id: number }).job_id }))
      .catch((e) => dispatch({ type: 'failed', error: toApiError(e) }));
  };

  const title = `Thử ${conn.name}`;
  const error = state.error ? (
    <p role="alert" className="text-sm text-state-failed">
      {state.error.detail}
      {state.error.hint ? ` ${state.error.hint}` : ''}
    </p>
  ) : null;

  if (state.step === 'estimate' || (state.step === 'running' && state.estimate)) {
    const e = state.estimate!;
    return (
      <EstimateDialog
        open
        title={`Ước tính chi phí · ${conn.name}`}
        rows={estimateRows(e)}
        jobCount={1}
        budgetRemaining={e.budget?.remaining ?? null}
        running={state.step === 'running'}
        offline={offline}
        onClose={() => dispatch({ type: 'back' })}
        onRun={runNow}
        warnings={[
          ...(e.enabled ? [] : ['Kết nối đang tắt — việc sẽ thất bại với lỗi provider không dùng được. Bật kết nối trước khi thử.']),
          ...(e.adapter.startsWith('fake_') ? ['Kết nối giả: không tốn lượt thật, không tính vào ngân sách.'] : []),
          ...(state.error ? [`${state.error.detail}${state.error.hint ? ` ${state.error.hint}` : ''}`] : []),
        ]}
      />
    );
  }

  if (state.step === 'confirm' || (state.step === 'running' && !state.estimate)) {
    return (
      <Modal
        open
        title={`Chạy thử · ${conn.name}`}
        onClose={state.step === 'running' ? undefined : () => dispatch({ type: 'back' })}
        actions={
          <>
            <Button onClick={() => dispatch({ type: 'back' })} disabled={state.step === 'running'}>
              Quay lại
            </Button>
            <Button
              variant="primary"
              icon={Play}
              loading={state.step === 'running'}
              disabled={offline}
              onClick={runNow}
            >
              Chạy thử · 1 việc
            </Button>
          </>
        }
      >
        <div className="grid gap-3">
          <div className="sr-warnbox flex items-start gap-2 rounded-sm px-2.5 py-2 text-sm">
            <CircleAlert className="sr-ico mt-0.5 text-warn" aria-hidden />
            <span className="min-w-0">
              {KIND_LABEL[conn.kind]} chưa ước tính được chi phí trước khi chạy. Lần thử này gửi một yêu cầu tới
              provider {conn.adapter.startsWith('fake_') ? '(kết nối giả: không tốn lượt thật)' : 'và có thể tốn lượt'}.
            </span>
          </div>
          {error}
        </div>
      </Modal>
    );
  }

  return (
    <Modal
      open
      size="lg"
      title={title}
      onClose={onClose}
      actions={
        state.step === 'job' ? (
          <>
            <Button variant="ghost" onClick={() => navigate('/jobs')}>
              Mở trang Việc
            </Button>
            <Button onClick={() => dispatch({ type: 'reset' })}>Thử lần khác</Button>
            <Button variant="primary" onClick={onClose}>
              Xong
            </Button>
          </>
        ) : (
          <>
            <Button onClick={onClose}>Huỷ</Button>
            <Button
              variant="primary"
              icon={FlaskConical}
              loading={state.step === 'estimating'}
              disabled={!formReady(conn, form)}
              onClick={estimate}
            >
              {dryRun ? 'Ước tính chi phí' : 'Tiếp tục'}
            </Button>
          </>
        )
      }
    >
      {state.step === 'job' && state.jobId != null ? (
        <div className="grid gap-3">
          <p className="text-sm text-fg-2">
            Đã xếp việc <span className="font-mono">#{state.jobId}</span>. Tiến độ cập nhật sống ở đây và trên trang Việc.
          </p>
          <JobWatch jobId={state.jobId} />
        </div>
      ) : (
        <form
          className="grid gap-4"
          noValidate
          onSubmit={(e) => {
            e.preventDefault();
            estimate();
          }}
        >
          {conn.kind === 'image' || conn.kind === 'video' || conn.kind === 'llm' ? (
            <Textarea
              id="try-prompt"
              label="Prompt"
              hint="Gửi nguyên văn tới provider."
              value={form.prompt}
              onChange={(e) => set('prompt', e.target.value)}
              showCount
              data-autofocus
            />
          ) : null}
          {conn.kind === 'tts' ? (
            <Textarea id="try-text" label="Câu cần đọc" value={form.text} onChange={(e) => set('text', e.target.value)} showCount data-autofocus />
          ) : null}
          <div className="grid gap-4 sm:grid-cols-2">
            {conn.kind === 'image' && list(c.aspects).length ? (
              <Select
                id="try-aspect"
                label="Tỉ lệ khung"
                value={form.aspect}
                onChange={(v) => set('aspect', v)}
                options={[{ value: '', label: 'Theo cài đặt' }, ...list(c.aspects).map((a) => ({ value: a, label: a }))]}
              />
            ) : null}
            {conn.kind === 'video' ? <MediaPicker kind="image" value={form.mediaId} onChange={(v) => set('mediaId', v)} /> : null}
            {conn.kind === 'video' && list(c.durations).length ? (
              <Select
                id="try-duration"
                label="Thời lượng"
                value={form.duration}
                onChange={(v) => set('duration', v)}
                options={[{ value: '', label: 'Theo cài đặt' }, ...list(c.durations).map((d) => ({ value: d, label: `${d} giây` }))]}
              />
            ) : null}
            {conn.kind === 'tts' ? (
              <Input id="try-voice" label="Giọng (tuỳ chọn)" placeholder="Theo cài đặt tts.voice" value={form.voice} onChange={(e) => set('voice', e.target.value)} />
            ) : null}
            {conn.kind === 'llm' ? (
              <Input
                id="try-model"
                label="Model (tuỳ chọn)"
                placeholder={conn.default_model || 'Model mặc định của kết nối'}
                value={form.model}
                onChange={(e) => set('model', e.target.value)}
              />
            ) : null}
            {conn.kind === 'stt' ? <MediaPicker kind="audio" value={form.mediaId} onChange={(v) => set('mediaId', v)} /> : null}
          </div>
          {!conn.enabled ? (
            <p className="text-sm text-warn">Kết nối đang tắt — bật trong Sửa kết nối để lần thử chạy được.</p>
          ) : null}
          {error}
          {state.estimate?.budget?.remaining != null ? (
            <p className="text-xs text-fg-2">Ngân sách còn {formatCredits(state.estimate.budget.remaining)}.</p>
          ) : null}
          <button type="submit" hidden aria-hidden tabIndex={-1} />
        </form>
      )}
    </Modal>
  );
}
