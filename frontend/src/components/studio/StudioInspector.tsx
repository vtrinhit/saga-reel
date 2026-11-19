/**
 * Inspector của Studio (DESIGN §5.4/§6.11): panel bg-1, mục ngăn bằng line-1 (không hộp lồng), chân dính. Nội dung theo
 * lựa chọn trên timeline: clip (thời lượng, chuyển cảnh vào, prompt chuyển động, âm gốc, thoại, Tạo lại video), câu
 * thoại (chữ, offset, phụ đề), nhạc/SFX/title (neo, offset, kết thúc, âm lượng, duck, fade, xoá), hoặc tập (thêm nhạc).
 */
import { AudioLines, Music, Plus, RefreshCw, Trash2 } from 'lucide-react';
import { useEffect, useState, type ReactNode } from 'react';
import { Link } from 'react-router';
import { cn } from '../../lib/cn';
import type { SoundRow } from '../../lib/studioData';
import {
  clipSeconds,
  shortTime,
  type ClipRow,
  type ClipSlot,
  type DialogueRow,
  type Placed,
  type TimelineItemRow,
  type TransitionTable,
} from '../../lib/timeline';
import { Button } from '../ui/Button';
import { Checkbox } from '../ui/Checkbox';
import { Input } from '../ui/Input';
import { SegmentedControl } from '../ui/SegmentedControl';
import { Select } from '../ui/Select';
import { StatusPill } from '../ui/StatusPill';
import { Textarea } from '../ui/Textarea';

export function InspectorSection({ title, children, extra }: { title: string; children: ReactNode; extra?: ReactNode }) {
  return (
    <section className="grid gap-2 border-b border-line-1 px-3 py-3 last:border-b-0">
      <div className="flex min-w-0 items-center justify-between gap-2">
        <h3 className="truncate py-px text-sm font-semibold">{title}</h3>
        {extra}
      </div>
      {children}
    </section>
  );
}

const TRANSITION_LABEL: Record<string, string> = {
  cut: 'Cắt thẳng',
  fade: 'Mờ chồng',
  dissolve: 'Hoà tan',
  fadeblack: 'Qua đen',
  fadewhite: 'Qua trắng',
};

/** Ô số (giây, 1 chữ số thập phân) lưu khi rời ô / Enter. */
function SecondsField({ id, label, ms, onCommit, hint, min = 0.1, disabled }: {
  id: string;
  label: string;
  ms: number | null;
  onCommit: (ms: number | null) => void;
  hint?: ReactNode;
  min?: number;
  disabled?: boolean;
}) {
  const fmt = (v: number | null) => (v == null ? '' : (Math.round(v / 100) / 10).toFixed(1).replace('.', ','));
  const [text, setText] = useState(fmt(ms));
  useEffect(() => setText(fmt(ms)), [ms]);
  const commit = () => {
    const t = text.trim().replace(',', '.');
    if (t === '') return ms == null ? undefined : onCommit(null);
    const v = Number(t);
    if (!Number.isFinite(v) || v < min) return setText(fmt(ms));
    const next = Math.round(v * 1000);
    if (next !== ms) onCommit(next);
  };
  return (
    <Input
      id={id}
      label={label}
      hint={hint}
      inputMode="decimal"
      value={text}
      disabled={disabled}
      onChange={(e) => setText(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === 'Enter') commit();
      }}
      trailing={<span className="text-xs text-fg-3">giây</span>}
    />
  );
}

/** Ô chữ dài lưu khi rời ô. */
function CommitTextarea({ id, label, value, onCommit, hint, disabled }: {
  id: string;
  label: string;
  value: string;
  onCommit: (v: string) => void;
  hint?: ReactNode;
  disabled?: boolean;
}) {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  return (
    <Textarea
      id={id}
      label={label}
      hint={hint}
      value={text}
      disabled={disabled}
      minRows={3}
      maxRows={10}
      showCount
      onChange={(e) => setText(e.target.value)}
      onBlur={() => text !== value && onCommit(text)}
    />
  );
}

function clipStatus(c: ClipRow): { status: 'failed' | 'drifted' | 'stale' | 'approved' | 'done' | 'queued'; label?: string } {
  const f = (c.flags ?? {}) as Record<string, unknown>;
  if (f.failed_video || f.failed_image) return { status: 'failed' };
  if (f.drift) return { status: 'drifted' };
  if (f.stale_video || f.stale_image) return { status: 'stale' };
  if (c.review === 'approved') return { status: 'approved' };
  if (c.video_take_id || c.image_take_id) return { status: 'done' };
  return { status: 'queued', label: 'Chưa có ảnh' };
}

export interface ClipInspectorProps {
  clip: ClipRow;
  slot: ClipSlot | null;
  prevKey: string | null;
  lines: DialogueRow[];
  transitions: TransitionTable;
  readOnly?: boolean;
  audioHref: string;
  onUpdate: (values: Record<string, unknown>, label: string) => void;
  onRegenerate: () => void;
  regenerating?: boolean;
  /** Mất kết nối: khoá nút tốn phí (DESIGN §5.5 OfflineBanner). */
  offline?: boolean;
}

export function ClipInspector({ clip, slot, prevKey, lines, transitions, readOnly, audioHref, onUpdate, onRegenerate, regenerating, offline }: ClipInspectorProps) {
  const st = clipStatus(clip);
  const tr = (clip.transition ?? {}) as { name?: string; duration_ms?: number | null };
  const trName = tr.name || (clip.link === 'fade' ? 'fade' : 'cut');
  const names = Array.from(new Set(['cut', ...Object.keys(transitions)]));
  const grok = clip.grok_audio || 'ambient';
  const motion = clip.motion_prompt ?? '';
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto">
        <InspectorSection title={`${clip.key}${clip.title ? ` · ${clip.title}` : ''} · ${clipSeconds(slot?.dur_ms ?? clip.duration_ms)}`}>
          <div className="flex flex-wrap items-center gap-2 text-xs text-fg-2">
            <StatusPill status={st.status} label={st.label} />
            {slot ? (
              <span className="font-mono tabular-nums">
                {shortTime(slot.start_ms)}–{shortTime(slot.end_ms)}
              </span>
            ) : null}
            <span>engine {clip.engine ?? '—'}</span>
          </div>
        </InspectorSection>
        <InspectorSection title="Thời lượng">
          <SecondsField
            id={`dur-${clip.id}`}
            label="Độ dài clip"
            ms={slot?.dur_ms ?? clip.duration_ms}
            min={0.5}
            disabled={readOnly}
            hint={
              clip.video_take_id != null
                ? 'Clip có video: dài theo video nguồn sau trim — đổi ở đây là đổi điểm ra. Phím , . trim từng khung.'
                : 'Kéo mép phải trên timeline hoặc phím , . để trim từng khung.'
            }
            onCommit={(ms) => ms != null && onUpdate({ duration_ms: ms }, 'thời lượng')}
          />
        </InspectorSection>
        <InspectorSection title={prevKey ? `Chuyển cảnh từ ${prevKey}` : 'Chuyển cảnh vào'}>
          {prevKey ? (
            <>
              <Select
                id={`tr-${clip.id}`}
                label="Kiểu"
                value={trName}
                disabled={readOnly}
                options={names.map((n) => ({ value: n, label: TRANSITION_LABEL[n] ?? n }))}
                onChange={(v) => onUpdate({ transition: v === 'cut' ? { name: 'cut' } : { ...tr, name: v } }, 'chuyển cảnh')}
              />
              {trName !== 'cut' ? (
                <SecondsField
                  id={`trd-${clip.id}`}
                  label="Độ dài chồng"
                  ms={tr.duration_ms ?? null}
                  min={0}
                  disabled={readOnly}
                  hint={
                    slot && slot.overlap_ms
                      ? `Đang chồng ${clipSeconds(slot.overlap_ms)}; để trống = theo preset (sang cảnh mới dài hơn), tối đa nửa clip ngắn hơn.`
                      : 'Để trống = theo preset.'
                  }
                  onCommit={(ms) => onUpdate({ transition: { ...tr, name: trName, duration_ms: ms } }, 'độ dài chuyển cảnh')}
                />
              ) : null}
            </>
          ) : (
            <p className="text-sm text-fg-2">Clip đầu tập không có chuyển cảnh vào.</p>
          )}
        </InspectorSection>
        <InspectorSection title="Prompt chuyển động">
          <CommitTextarea
            id={`mp-${clip.id}`}
            label="Prompt gửi tạo video"
            value={motion}
            disabled={readOnly}
            hint="Sửa prompt đánh dấu video là đã cũ; bấm Tạo lại video để có take mới."
            onCommit={(v) => onUpdate({ motion_prompt: v }, 'prompt chuyển động')}
          />
        </InspectorSection>
        <InspectorSection title="Âm gốc của video">
          <SegmentedControl
            label="Âm gốc của video"
            value={grok as 'keep' | 'ambient' | 'mute'}
            options={[
              { value: 'keep', label: 'Giữ', disabled: readOnly },
              { value: 'ambient', label: 'Nền −18 dB', disabled: readOnly },
              { value: 'mute', label: 'Tắt', disabled: readOnly },
            ]}
            onChange={(v) => onUpdate({ grok_audio: v }, 'âm gốc')}
          />
        </InspectorSection>
        <InspectorSection
          title={`Thoại (${lines.length})`}
          extra={
            <Link to={audioHref} className="text-xs text-fg-2 underline-offset-2 hover:text-fg-1 hover:underline">
              Mở Âm thanh
            </Link>
          }
        >
          {lines.length ? (
            <ul className="grid gap-1.5">
              {lines.map((l) => (
                <li key={l.id} className="flex gap-2 text-sm">
                  <AudioLines className="mt-0.5 size-4 shrink-0 text-track-dialogue" aria-hidden />
                  <span className="min-w-0">{l.text || <span className="text-fg-3">(trống)</span>}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-fg-2">Clip này chưa có thoại.</p>
          )}
        </InspectorSection>
      </div>
      {!readOnly ? (
        <div className="flex justify-end gap-2 border-t border-line-1 bg-bg-1 px-3 py-2">
          <Button
            variant="primary"
            icon={RefreshCw}
            loading={regenerating}
            disabled={offline}
            title={offline ? 'Mất kết nối: tạm khoá thao tác tốn phí' : undefined}
            onClick={onRegenerate}
            cost={<span className="font-mono text-2xs opacity-80">ước tính trước</span>}
          >
            Tạo lại video
          </Button>
        </div>
      ) : null}
    </div>
  );
}

export function LineInspector({ line, placed, readOnly, onUpdate }: {
  line: DialogueRow;
  placed: Placed | null;
  readOnly?: boolean;
  onUpdate: (values: Partial<DialogueRow>) => void;
}) {
  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <InspectorSection title={`Câu thoại #${line.id}`}>
        <p className="font-mono text-xs tabular-nums text-fg-2">
          {placed ? `${shortTime(placed.start_ms)}–${shortTime(placed.end_ms)} · ${clipSeconds(placed.dur_ms)}` : 'Không nằm trong tập'}
          {line.audio_take_id ? '' : ' · chưa đọc (độ dài ước tính)'}
        </p>
      </InspectorSection>
      <InspectorSection title="Lời">
        <CommitTextarea id={`lt-${line.id}`} label="Lời thoại (gửi TTS nguyên văn)" value={line.text} disabled={readOnly} onCommit={(v) => onUpdate({ text: v })} />
      </InspectorSection>
      <InspectorSection title="Vị trí và phụ đề">
        <SecondsField
          id={`lo-${line.id}`}
          label="Bắt đầu sau đầu clip"
          ms={line.offset_ms}
          min={0}
          disabled={readOnly}
          onCommit={(ms) => onUpdate({ offset_ms: ms ?? 0 })}
        />
        <Checkbox label="Hiện phụ đề" checked={line.subtitle} disabled={readOnly} onChange={(e) => onUpdate({ subtitle: e.target.checked })} />
      </InspectorSection>
    </div>
  );
}

type EndMode = 'media' | 'clip' | 'sequence' | 'fixed';

export function ItemInspector({ item, placed, clips, sound, readOnly, onUpdate, onRemove }: {
  item: TimelineItemRow;
  placed: Placed | null;
  clips: ClipRow[];
  sound: SoundRow | null;
  readOnly?: boolean;
  onUpdate: (values: Partial<TimelineItemRow>) => void;
  onRemove: () => void;
}) {
  const p = item.params ?? {};
  const endMode: EndMode =
    item.duration_ms != null ? 'fixed' : item.end_anchor?.sequence_end ? 'sequence' : item.end_anchor?.clip_id != null ? 'clip' : 'media';
  const clipOpts = clips.map((c) => ({ value: String(c.id), label: `${c.key}${c.title ? ` · ${c.title}` : ''}` }));
  const kindLabel = item.track === 'music' ? 'Nhạc' : item.track === 'sfx' ? 'Hiệu ứng âm' : 'Title';
  const setParam = (k: string, v: unknown) => onUpdate({ params: { ...p, [k]: v } });
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto">
        <InspectorSection title={`${kindLabel}${sound ? ` · ${sound.title}` : ''}`}>
          <p className="font-mono text-xs tabular-nums text-fg-2">
            {placed ? `${shortTime(placed.start_ms)}–${shortTime(placed.end_ms)} · ${clipSeconds(placed.dur_ms)}` : 'Ngoài thời lượng tập (bị bỏ khi dựng)'}
          </p>
          {sound ? (
            <p className="text-xs text-fg-2">
              {sound.license}
              {sound.attribution ? ` · ${sound.attribution}` : ''} · {sound.source}
            </p>
          ) : null}
        </InspectorSection>
        <InspectorSection title="Neo vào clip">
          <Select
            id={`anc-${item.id}`}
            label="Bắt đầu theo clip"
            value={item.anchor_clip_id != null ? String(item.anchor_clip_id) : '0'}
            disabled={readOnly}
            options={[{ value: '0', label: 'Đầu tập' }, ...clipOpts]}
            onChange={(v) => onUpdate({ anchor_clip_id: v === '0' ? null : Number(v) })}
            hint="Clip dời chỗ hay đổi độ dài thì mục này đi theo."
          />
          <SecondsField id={`off-${item.id}`} label="Sau đầu clip neo" ms={item.offset_ms} min={0} disabled={readOnly} onCommit={(ms) => onUpdate({ offset_ms: ms ?? 0 })} />
          <Select<EndMode>
            id={`end-${item.id}`}
            label="Kết thúc"
            value={endMode}
            disabled={readOnly}
            options={[
              { value: 'media', label: item.track === 'music' ? 'Theo độ dài file (nhạc lặp: hết tập)' : 'Theo độ dài file' },
              { value: 'clip', label: 'Hết một clip' },
              { value: 'sequence', label: 'Hết tập' },
              { value: 'fixed', label: 'Số giây cố định' },
            ]}
            onChange={(v) => {
              if (v === 'media') onUpdate({ duration_ms: null, end_anchor: null });
              else if (v === 'sequence') onUpdate({ duration_ms: null, end_anchor: { sequence_end: true } });
              else if (v === 'clip') onUpdate({ duration_ms: null, end_anchor: { clip_id: item.anchor_clip_id ?? clips[0]?.id } });
              else onUpdate({ duration_ms: placed?.dur_ms || 3000, end_anchor: null });
            }}
          />
          {endMode === 'clip' ? (
            <Select
              id={`endc-${item.id}`}
              label="Hết clip"
              value={String(item.end_anchor?.clip_id ?? '')}
              disabled={readOnly}
              options={clipOpts}
              onChange={(v) => onUpdate({ end_anchor: { clip_id: Number(v) } })}
            />
          ) : null}
          {endMode === 'fixed' ? (
            <SecondsField id={`dur-${item.id}`} label="Độ dài" ms={item.duration_ms} disabled={readOnly} onCommit={(ms) => ms != null && onUpdate({ duration_ms: ms })} />
          ) : null}
        </InspectorSection>
        {item.track !== 'title' ? (
          <InspectorSection title="Trộn">
            <Input
              id={`vol-${item.id}`}
              label="Âm lượng"
              type="number"
              step={1}
              defaultValue={typeof p.volume_db === 'number' ? p.volume_db : item.track === 'music' ? -18 : 0}
              disabled={readOnly}
              onBlur={(e) => setParam('volume_db', Number(e.target.value))}
              trailing={<span className="text-xs text-fg-3">dB</span>}
            />
            {item.track === 'music' ? (
              <>
                <Checkbox label="Hạ nhạc khi có thoại (duck)" checked={p.duck !== false} disabled={readOnly} onChange={(e) => setParam('duck', e.target.checked)} />
                <Checkbox label="Lặp tới hết tập" checked={Boolean(p.loop)} disabled={readOnly} onChange={(e) => setParam('loop', e.target.checked)} />
              </>
            ) : null}
            <div className="grid grid-cols-2 gap-2">
              <Input
                id={`fi-${item.id}`}
                label="Fade vào"
                inputMode="decimal"
                defaultValue={String(p.fade_in_s ?? 0).replace('.', ',')}
                disabled={readOnly}
                onBlur={(e) => setParam('fade_in_s', Number(e.target.value.replace(',', '.')) || 0)}
                trailing={<span className="text-xs text-fg-3">s</span>}
              />
              <Input
                id={`fo-${item.id}`}
                label="Fade ra"
                inputMode="decimal"
                defaultValue={String(p.fade_out_s ?? 0).replace('.', ',')}
                disabled={readOnly}
                onBlur={(e) => setParam('fade_out_s', Number(e.target.value.replace(',', '.')) || 0)}
                trailing={<span className="text-xs text-fg-3">s</span>}
              />
            </div>
          </InspectorSection>
        ) : (
          <InspectorSection title="Chữ">
            <CommitTextarea id={`tt-${item.id}`} label="Nội dung title" value={String(p.text ?? '')} disabled={readOnly} onCommit={(v) => setParam('text', v)} />
          </InspectorSection>
        )}
      </div>
      {!readOnly ? (
        <div className="flex justify-end gap-2 border-t border-line-1 bg-bg-1 px-3 py-2">
          <Button variant="danger" icon={Trash2} onClick={onRemove}>
            Xoá khỏi timeline
          </Button>
        </div>
      ) : null}
    </div>
  );
}

/** Không chọn gì: tóm tắt tập + thêm nhạc/SFX/title tại playhead. */
export function SequencePanel({ totalMs, clipCount, warnings, sounds, readOnly, playheadMs, onAdd, libraryHref }: {
  totalMs: number;
  clipCount: number;
  warnings: { code: string; message: string }[];
  sounds: SoundRow[];
  readOnly?: boolean;
  playheadMs: number;
  onAdd: (track: 'music' | 'sfx' | 'title', soundId: number | null, text?: string) => void;
  libraryHref: string;
}) {
  const [soundId, setSoundId] = useState<string | null>(null);
  const [title, setTitle] = useState('');
  const chosen = sounds.find((s) => String(s.id) === soundId) ?? null;
  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <InspectorSection title="Tập">
        <p className="text-sm text-fg-2">
          {clipCount} clip · <span className="font-mono tabular-nums">{shortTime(totalMs)}</span> sau chuyển cảnh. Chọn một mục trên timeline để sửa.
        </p>
      </InspectorSection>
      {warnings.length ? (
        <InspectorSection title={`Cảnh báo dựng (${warnings.length})`}>
          <ul className="grid gap-1 text-sm">
            {warnings.slice(0, 8).map((w, i) => (
              <li key={i} className="text-warn">
                {w.message}
              </li>
            ))}
          </ul>
        </InspectorSection>
      ) : null}
      {!readOnly ? (
        <InspectorSection
          title="Thêm âm thanh tại playhead"
          extra={
            <Link to={libraryHref} className="text-xs text-fg-2 underline-offset-2 hover:text-fg-1 hover:underline">
              Kho âm thanh
            </Link>
          }
        >
          {sounds.length ? (
            <>
              <Select
                id="add-sound"
                label="Âm thanh trong kho"
                placeholder="Chọn nhạc hoặc SFX"
                value={soundId}
                options={sounds.map((s) => ({
                  value: String(s.id),
                  label: s.title,
                  description: `${s.kind === 'music' ? 'Nhạc' : s.kind === 'sfx' ? 'SFX' : 'Nền'} · ${s.license}`,
                }))}
                onChange={setSoundId}
              />
              <Button
                icon={chosen?.kind === 'music' ? Music : Plus}
                disabled={!chosen}
                onClick={() => chosen && onAdd(chosen.kind === 'music' ? 'music' : 'sfx', chosen.id)}
              >
                Thêm tại {shortTime(playheadMs)}
              </Button>
            </>
          ) : (
            <p className="text-sm text-fg-2">Kho chưa có âm thanh. Thêm nhạc kèm nguồn và giấy phép ở trang Âm thanh.</p>
          )}
          <div className={cn('grid gap-2 border-t border-line-1 pt-3')}>
            <Input id="add-title" label="Title chữ" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Chữ hiện trên hình" />
            <Button icon={Plus} disabled={!title.trim()} onClick={() => (onAdd('title', null, title.trim()), setTitle(''))}>
              Thêm title tại {shortTime(playheadMs)}
            </Button>
          </div>
        </InspectorSection>
      ) : null}
    </div>
  );
}
