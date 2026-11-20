import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Calculator, Plus, RotateCcw, Trash2, TriangleAlert } from 'lucide-react';
import { useEffect, useId, useState } from 'react';
import { useParams } from 'react-router';
import { callOp } from '../../api/ops';
import { useEnumOptions } from '../../lib/episodeData';
import { undoAction } from '../../lib/episodeUndo';
import { cn } from '../../lib/cn';
import type { AssetBrief, Clip, DialogueLine, PromptPreview, Take } from '../../lib/episodeTypes';
import {
  SHOT_FIELD_LABEL, formatClipSeconds, type ClipView, type ShotField,
} from '../../lib/episodeView';
import { toast } from '../../stores/toast';
import { Button } from '../ui/Button';
import { Inspector, InspectorSection } from '../ui/Inspector';
import { SegmentedControl } from '../ui/SegmentedControl';
import { StatusPill } from '../ui/StatusPill';
import { TakeStrip } from './TakeStrip';
import { errText } from './useRegenerate';

const SHOT_FIELDS: ShotField[] = ['shot_size', 'angle', 'camera_move', 'lens', 'composition', 'eyeline', 'screen_dir'];

export interface ClipInspectorProps {
  clip: Clip;
  index: number;
  sceneNo: string;
  view: ClipView;
  part: 'image' | 'video';
  assets: Map<number, AssetBrief>;
  onSave: (clip: Clip, values: Partial<Clip>) => Promise<void>;
  onRegenerate: (clip: Clip) => void;
  onEstimate: (clip: Clip) => void;
  bare?: boolean;
  className?: string;
}

/** Inspector clip (DESIGN §5.4/§6.11): Prompt · Shot · Tài sản · Thoại · Take; chân dính Ước tính · Tạo lại. */
export function ClipInspector(p: ClipInspectorProps) {
  const { clip: c, view } = p;
  const title = `${p.sceneNo} · Clip ${p.index + 1}`;
  return (
    <Inspector
      bare={p.bare}
      className={p.className}
      title={title}
      meta={
        <>
          {view.status === 'empty' ? <span className="text-fg-3">Chưa tạo</span> : <StatusPill status={view.status} label={view.label} />}
          <span className="font-mono tnum">{c.key}</span>
          <span className="font-mono tnum">{formatClipSeconds(c.duration_ms)}</span>
        </>
      }
      footer={
        <>
          <Button icon={Calculator} onClick={() => p.onEstimate(c)}>
            Ước tính chi phí
          </Button>
          <Button variant="primary" icon={RotateCcw} onClick={() => p.onRegenerate(c)}>
            Tạo lại {p.part === 'image' ? 'ảnh' : 'video'}
          </Button>
        </>
      }
    >
      {view.error ? (
        <div role="alert" className="flex gap-2 border-b border-line-1 px-3 py-2 text-sm text-state-failed">
          <TriangleAlert className="sr-ico mt-0.5 flex-none" aria-hidden />
          <span className="min-w-0">{view.error}</span>
        </div>
      ) : null}
      <PromptSection {...p} />
      <ShotSection {...p} />
      <AssetSection clip={c} assets={p.assets} />
      <DialogueSection clip={c} assets={p.assets} />
      <TakeSection clip={c} part={p.part} />
    </Inspector>
  );
}

function PromptSection({ clip: c, onSave }: ClipInspectorProps) {
  const id = useId();
  const [img, setImg] = useState(c.image_prompt);
  const [mot, setMot] = useState(c.motion_prompt);
  useEffect(() => setImg(c.image_prompt), [c.id, c.image_prompt]);
  useEffect(() => setMot(c.motion_prompt), [c.id, c.motion_prompt]);
  const [showFinal, setShowFinal] = useState(false);
  const preview = useQuery({
    queryKey: ['episode', 'preview', c.id, c.updated_at],
    queryFn: async () => (await callOp('clip.preview_prompt', { clip_id: c.id, part: 'image' } as never)) as unknown as PromptPreview,
    enabled: showFinal,
    staleTime: 60_000,
    retry: false,
  });
  const src = c.image_prompt_src as { source?: string; actor?: string; model?: string };
  return (
    <InspectorSection title="Prompt" extra={src?.source ? `nguồn: ${src.actor ?? src.source}` : undefined}>
      <SegmentedControl
        label="Chế độ prompt"
        options={[
          { value: 'verbatim', label: 'Nguyên văn' },
          { value: 'synced', label: 'Đồng bộ' },
        ]}
        value={c.prompt_mode}
        onChange={(v) => void onSave(c, { prompt_mode: v }).catch(() => undefined)}
      />
      <label htmlFor={`${id}-img`} className="text-xs text-fg-2">Prompt ảnh</label>
      <textarea
        id={`${id}-img`}
        value={img}
        rows={4}
        onChange={(e) => setImg(e.target.value)}
        onBlur={() => img !== c.image_prompt && void onSave(c, { image_prompt: img }).catch(() => setImg(c.image_prompt))}
        className="w-full resize-y rounded-sm border border-line-3 bg-bg-2 px-2 py-1.5 text-sm text-fg-1 focus-visible:outline focus-visible:outline-focus focus-visible:outline-accent md:text-sm"
      />
      <label htmlFor={`${id}-mot`} className="text-xs text-fg-2">Prompt chuyển động</label>
      <textarea
        id={`${id}-mot`}
        value={mot}
        rows={2}
        onChange={(e) => setMot(e.target.value)}
        onBlur={() => mot !== c.motion_prompt && void onSave(c, { motion_prompt: mot }).catch(() => setMot(c.motion_prompt))}
        className="w-full resize-y rounded-sm border border-line-3 bg-bg-2 px-2 py-1.5 text-sm text-fg-1 focus-visible:outline focus-visible:outline-focus focus-visible:outline-accent"
      />
      <details onToggle={(e) => setShowFinal((e.target as HTMLDetailsElement).open)} className="text-sm">
        <summary className="cursor-pointer text-fg-2 hover:text-fg-1">Prompt cuối</summary>
        {preview.isPending && showFinal ? <p className="mt-2 text-xs text-fg-3">Đang ghép prompt…</p> : null}
        {preview.isError ? <p className="mt-2 text-xs text-state-failed">{errText(preview.error)}</p> : null}
        {preview.data ? (
          <div className="mt-2 grid gap-2">
            <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-sm bg-stage p-2 font-mono text-xs leading-5 text-fg-2">
              {preview.data.text}
            </pre>
            {preview.data.refs?.length ? (
              <p className="text-xs text-fg-2">{preview.data.refs.length} ảnh tham chiếu: {preview.data.refs.map((r) => r.label ?? r.role ?? `#${r.media_id}`).join(', ')}</p>
            ) : (
              <p className="text-xs text-fg-3">Không có ảnh tham chiếu.</p>
            )}
            {preview.data.warnings?.map((w, i) => (
              <p key={i} className="flex gap-1.5 text-xs text-warn">
                <TriangleAlert className="mt-0.5 h-3 w-3 flex-none" aria-hidden />
                {w}
              </p>
            ))}
          </div>
        ) : null}
      </details>
    </InspectorSection>
  );
}

function ShotSection({ clip: c, onSave }: ClipInspectorProps) {
  const enums = useEnumOptions();
  const id = useId();
  const [dur, setDur] = useState(String(c.duration_ms / 1000));
  useEffect(() => setDur(String(c.duration_ms / 1000)), [c.id, c.duration_ms]);
  const sel = 'h-control w-full min-w-0 rounded-sm border border-line-3 bg-bg-2 px-2 text-sm focus-visible:outline focus-visible:outline-focus focus-visible:outline-accent';
  return (
    <InspectorSection title="Shot" defaultOpen>
      <div className="grid grid-cols-2 gap-2">
        {SHOT_FIELDS.map((f) => (
          <label key={f} className="grid min-w-0 gap-1 text-xs text-fg-2">
            {SHOT_FIELD_LABEL[f]}
            <select value={c[f]} onChange={(e) => void onSave(c, { [f]: e.target.value } as Partial<Clip>).catch(() => undefined)} className={sel}>
              <option value="">Chưa đặt</option>
              {enums.shot[f].map(([k, label]) => (
                <option key={k} value={k}>{label}</option>
              ))}
            </select>
          </label>
        ))}
        <label className="grid min-w-0 gap-1 text-xs text-fg-2" htmlFor={`${id}-d`}>
          Thời lượng (giây)
          <input
            id={`${id}-d`}
            inputMode="decimal"
            value={dur}
            onChange={(e) => setDur(e.target.value)}
            onBlur={() => {
              const s = Number(dur.replace(',', '.'));
              if (!Number.isFinite(s) || s <= 0) return setDur(String(c.duration_ms / 1000));
              const ms = Math.round(s * 1000);
              if (ms !== c.duration_ms) void onSave(c, { duration_ms: ms }).catch(() => undefined);
            }}
            className={cn(sel, 'font-mono tnum')}
          />
        </label>
        <label className="grid min-w-0 gap-1 text-xs text-fg-2">
          Engine
          <select value={c.engine} onChange={(e) => void onSave(c, { engine: e.target.value as Clip['engine'] }).catch(() => undefined)} className={sel}>
            {enums.engine.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
        </label>
        <label className="grid min-w-0 gap-1 text-xs text-fg-2">
          Nối với clip trước
          <select value={c.link} onChange={(e) => void onSave(c, { link: e.target.value as Clip['link'] }).catch(() => undefined)} className={sel}>
            {enums.link.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
        </label>
      </div>
    </InspectorSection>
  );
}

function AssetSection({ clip: c, assets }: { clip: Clip; assets: Map<number, AssetBrief> }) {
  return (
    <InspectorSection title="Tài sản" extra={c.assets.length ? `${c.assets.length}` : undefined} defaultOpen={c.assets.length > 0}>
      {c.assets.length === 0 ? <p className="text-xs text-fg-3">Clip chưa gắn nhân vật hay bối cảnh. Gắn ở Thư viện hoặc khi nhập hàng loạt.</p> : null}
      <div className="flex flex-wrap gap-1.5">
        {c.assets.map((a, i) => {
          const as = assets.get(a.asset_id);
          const look = as?.looks?.find((l) => l.id === a.variant_id);
          return (
            <span key={i} className="inline-flex h-6 items-center gap-1.5 rounded-sm bg-bg-3 px-2 text-xs">
              <span className="font-mono text-2xs text-fg-2">{as?.key ?? `#${a.asset_id}`}</span>
              {as?.name ?? ''}
              {look ? <span className="text-fg-2">· {look.name}</span> : null}
            </span>
          );
        })}
      </div>
    </InspectorSection>
  );
}

function DialogueSection({ clip: c, assets }: { clip: Clip; assets: Map<number, AssetBrief> }) {
  const qc = useQueryClient();
  const key = ['episode', 'dialogue', c.id];
  const q = useQuery({
    queryKey: key,
    queryFn: async () => ((await callOp('dialogue.list', { clip_id: c.id })) as unknown as { items: DialogueLine[] }).items,
    staleTime: 30_000,
  });
  const [draft, setDraft] = useState('');
  const refresh = () => void qc.invalidateQueries({ queryKey: key });
  const add = async () => {
    const text = draft.trim();
    if (!text) return;
    try {
      await callOp('dialogue.create', { clip_id: c.id, text });
      setDraft('');
      refresh();
    } catch (e) {
      toast({ message: `Không thêm được câu thoại: ${errText(e)}`, tone: 'error' });
    }
  };
  const save = async (d: DialogueLine, text: string) => {
    if (text === d.text) return;
    try {
      await callOp('dialogue.update', { id: d.id, values: { text }, expected_updated_at: d.updated_at });
      refresh();
    } catch (e) {
      toast({ message: `Không lưu được câu thoại: ${errText(e)}`, tone: 'error' });
      refresh();
    }
  };
  const remove = async (d: DialogueLine) => {
    try {
      const prev = (await callOp('dialogue.delete_preview', { ids: [d.id] })) as unknown as { confirm_token: string };
      await callOp('dialogue.delete', { confirm_token: prev.confirm_token });
      refresh();
      toast({
        message: 'Đã xoá câu thoại.',
        undo: () => void callOp('dialogue.create', { clip_id: c.id, text: d.text, speaker_asset_id: d.speaker_asset_id }).then(refresh),
      });
    } catch (e) {
      toast({ message: `Không xoá được câu thoại: ${errText(e)}`, tone: 'error' });
    }
  };
  const lines = q.data ?? [];
  return (
    <InspectorSection title="Thoại" extra={lines.length ? `${lines.length} câu` : undefined} defaultOpen={lines.length > 0}>
      {q.isError ? <p className="text-xs text-state-failed">{errText(q.error)}</p> : null}
      <ul className="grid gap-1.5">
        {lines.map((d) => (
          <li key={d.id} className="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-1">
            <div className="min-w-0">
              <span className="text-xs text-fg-2">{d.speaker_asset_id ? (assets.get(d.speaker_asset_id)?.name ?? `#${d.speaker_asset_id}`) : 'Người kể'}</span>
              <input
                defaultValue={d.text}
                aria-label="Câu thoại"
                onBlur={(e) => void save(d, e.target.value)}
                className="h-control w-full rounded-sm border border-line-3 bg-bg-2 px-2 text-sm focus-visible:outline focus-visible:outline-focus focus-visible:outline-accent"
              />
            </div>
            <button
              type="button"
              aria-label="Xoá câu thoại"
              onClick={() => void remove(d)}
              className="mt-5 grid h-control w-8 place-items-center rounded-sm text-fg-2 hover:bg-bg-3 hover:text-state-failed focus-visible:outline focus-visible:outline-focus focus-visible:outline-accent"
            >
              <Trash2 className="sr-ico" aria-hidden />
            </button>
          </li>
        ))}
      </ul>
      <div className="flex gap-1">
        <input
          value={draft}
          placeholder="Thêm câu thoại (người kể)"
          aria-label="Câu thoại mới"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && void add()}
          className="h-control min-w-0 flex-1 rounded-sm border border-line-3 bg-bg-2 px-2 text-sm focus-visible:outline focus-visible:outline-focus focus-visible:outline-accent"
        />
        <Button icon={Plus} onClick={() => void add()} disabled={!draft.trim()}>
          Thêm
        </Button>
      </div>
    </InspectorSection>
  );
}

function TakeSection({ clip: c, part }: { clip: Clip; part: 'image' | 'video' }) {
  const qc = useQueryClient();
  const pid = Number(useParams().projectId);
  const q = useQuery({
    queryKey: ['episode', 'takes', c.id, part, c.updated_at],
    queryFn: async () =>
      (await callOp('take.list', { owner_type: 'clip', owner_id: c.id, part })) as unknown as {
        items: Take[];
        selected: Record<string, number | null>;
      },
    staleTime: 15_000,
  });
  const [busy, setBusy] = useState<number | null>(null);
  const selectedId = q.data?.selected?.[part] ?? (part === 'image' ? c.image_take_id : c.video_take_id);
  const pick = async (t: Take) => {
    if (t.id === selectedId) return;
    setBusy(t.id);
    try {
      const out = await callOp('take.select', { take_id: t.id });
      void qc.invalidateQueries({ queryKey: ['episode', c.sequence_id] });
      void qc.invalidateQueries({ queryKey: ['episode', 'takes', c.id] });
      toast({
        message: 'Đã chọn take.',
        undo: undoAction(pid, out, () => {
          void qc.invalidateQueries({ queryKey: ['episode', c.sequence_id] });
          void qc.invalidateQueries({ queryKey: ['episode', 'takes', c.id] });
        }),
      });
    } catch (e) {
      toast({ message: `Không lưu được lựa chọn take: ${errText(e)}`, tone: 'error' });
    } finally {
      setBusy(null);
    }
  };
  const items = q.data?.items ?? [];
  const cost = items.reduce((s, t) => s + (t.cost || 0), 0);
  return (
    <InspectorSection title="Take" extra={items.length ? `${items.length} take · ${cost.toLocaleString('vi-VN')} lượt` : undefined}>
      {q.isError ? <p className="text-xs text-state-failed">{errText(q.error)}</p> : null}
      <TakeStrip takes={items} selectedId={selectedId ?? null} onSelect={(t) => void pick(t)} busyId={busy} />
    </InspectorSection>
  );
}
