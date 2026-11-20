import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Minus, Plus } from 'lucide-react';
import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { Link } from 'react-router';
import { toApiError } from '../../api/client';
import { useIsViewer } from '../../api/auth';
import { qk, useConcurrency } from '../../api/hooks';
import { callOp } from '../../api/ops';
import { ErrorState } from '../../components/ui/ErrorState';
import { IconButton } from '../../components/ui/IconButton';
import { SkeletonRows, SkeletonZone } from '../../components/ui/Skeleton';
import {
  CAP_MAX,
  CAP_MIN,
  clampCap,
  KIND_HINTS,
  usageText,
  withPending,
  type ConcurrencyKind,
  type ConcurrencyOverview,
  type KindUsage,
  type LaneUsage,
} from '../../lib/concurrency';
import { cn, focusRing } from '../../lib/cn';
import { toast } from '../../stores/toast';
import { SectionHeader } from './SettingsLayout';

/** Gộp các lần bấm +/− liên tiếp thành một lần ghi (một toast, một bước hoàn tác). */
export const COMMIT_DELAY_MS = 450;
export const APPLIED_MESSAGE = 'Đã áp dụng — việc mới dùng ngay số luồng này; việc đang chạy không bị dừng.';

type Caps = Partial<Record<ConcurrencyKind, number>>;

async function setCaps(values: Caps): Promise<ConcurrencyOverview> {
  return (await callOp('queue.set_concurrency', { values })) as unknown as ConcurrencyOverview;
}

/**
 * Dải ô luồng: mỗi ô là một luồng của trần; ô tô = đang chạy. Hạ trần khi việc còn chạy → phần vượt hiện thành ô viền
 * cảnh báo (việc đó làm nốt, không bị dừng).
 */
function SlotMeter({ running, cap, label }: { running: number; cap: number; label: string }) {
  const over = Math.max(0, running - cap);
  const slots = Array.from({ length: cap + over }, (_, i) => (i < Math.min(running, cap) ? 'busy' : i < cap ? 'free' : 'over'));
  return (
    <div
      role="img"
      aria-label={`${label}: ${running} trên ${cap} luồng đang chạy${over ? `, vượt trần ${over}` : ''}`}
      className="flex flex-wrap gap-[3px]"
    >
      {slots.map((s, i) => (
        <span
          key={i}
          className={cn(
            'h-4 w-2.5 rounded-xs border',
            s === 'busy' && 'border-state-running bg-state-running',
            s === 'free' && 'border-line-3 bg-bg-2',
            s === 'over' && 'sr-slot-over border-warn',
          )}
        />
      ))}
    </div>
  );
}

function Stepper({
  kind,
  label,
  value,
  disabled,
  onChange,
}: {
  kind: ConcurrencyKind;
  label: string;
  value: number;
  disabled: boolean;
  onChange: (n: number) => void;
}) {
  const [draft, setDraft] = useState(String(value));
  const editing = useRef(false);
  useEffect(() => {
    if (!editing.current) setDraft(String(value));
  }, [value]);
  const id = `cc-${kind}`;
  const commit = () => {
    editing.current = false;
    const n = clampCap(Number(draft));
    setDraft(String(n));
    if (n !== value) onChange(n);
  };
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && !e.nativeEvent.isComposing) commit();
  };
  return (
    <div className="flex items-center gap-1">
      <IconButton
        icon={Minus}
        label={`Bớt một luồng ${label}`}
        noTip
        disabled={disabled || value <= CAP_MIN}
        onClick={() => onChange(value - 1)}
      />
      <label htmlFor={id} className="sr-only">
        Số luồng {label}
      </label>
      <input
        id={id}
        name={id}
        type="number"
        inputMode="numeric"
        min={CAP_MIN}
        max={CAP_MAX}
        step={1}
        value={draft}
        disabled={disabled}
        aria-describedby={`${id}-hint`}
        onChange={(e) => {
          editing.current = true;
          setDraft(e.target.value);
        }}
        onBlur={commit}
        onKeyDown={onKey}
        className={cn(
          'sr-num h-control w-14 rounded-sm border border-line-3 bg-bg-2 text-center font-mono text-md tnum text-fg-1 md:text-base',
          'hover:border-fg-3 disabled:cursor-not-allowed disabled:border-line-1 disabled:bg-bg-1 disabled:text-fg-4',
          focusRing,
        )}
      />
      <IconButton
        icon={Plus}
        label={`Thêm một luồng ${label}`}
        noTip
        disabled={disabled || value >= CAP_MAX}
        onClick={() => onChange(value + 1)}
      />
    </div>
  );
}

function LaneLine({ lane }: { lane: LaneUsage }) {
  const busy = lane.running > 0 || lane.queued > 0;
  return (
    <li className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 py-1.5 text-xs">
      <span className="min-w-0 text-fg-2">
        <span className={cn('text-fg-1', !lane.enabled && 'text-fg-3')}>{lane.name}</span>
        {lane.enabled ? null : <span className="text-fg-3"> (đang tắt)</span>}
      </span>
      <span className="flex items-baseline gap-3 text-fg-2">
        <span className="tnum">
          {busy ? `${lane.running} chạy${lane.queued ? `, ${lane.queued} chờ` : ''}` : 'rảnh'}
          {`, trần riêng ${lane.cap}`}
        </span>
        {lane.connection_id != null ? (
          <Link
            to="/settings/providers"
            className={cn('sr-hit rounded-sm text-fg-1 underline decoration-line-3 underline-offset-2 hover:decoration-fg-2', focusRing)}
            aria-label={`Sửa trần riêng của kết nối ${lane.name}`}
          >
            Sửa
          </Link>
        ) : null}
      </span>
    </li>
  );
}

function KindRow({
  k,
  disabled,
  onChange,
}: {
  k: KindUsage;
  disabled: boolean;
  onChange: (n: number) => void;
}) {
  const over = k.running > k.cap;
  const lanes = k.connections;
  return (
    <section
      aria-labelledby={`cc-${k.kind}-title`}
      className="grid gap-3 border-b border-line-1 px-4 py-4 last:border-b-0 md:grid-cols-[minmax(0,1fr)_auto] md:gap-x-8"
    >
      <div className="grid min-w-0 gap-2">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h3 id={`cc-${k.kind}-title`} className="text-base font-medium">
            {k.label}
          </h3>
          <span className="text-sm text-fg-2 tnum">
            {usageText(k)}
          </span>
        </div>
        <p id={`cc-${k.kind}-hint`} className="text-xs text-fg-3">
          {KIND_HINTS[k.kind]}
        </p>
        <SlotMeter running={k.running} cap={k.cap} label={k.label} />
        {over ? (
          <p className="text-xs text-warn">
            Đang chạy nhiều hơn trần mới: các việc này làm nốt, việc mới chờ tới khi còn dưới {k.cap}.
          </p>
        ) : null}
      </div>
      <div className="md:row-span-2 md:self-start">
        <Stepper kind={k.kind} label={k.label} value={k.cap} disabled={disabled} onChange={onChange} />
      </div>
      {lanes.length ? (
        <ul className="min-w-0 divide-y divide-line-1 border-l-2 border-line-2 pl-3" aria-label={`Lane ${k.label}`}>
          {lanes.map((l) => (
            <LaneLine key={l.lane} lane={l} />
          ))}
        </ul>
      ) : (
        <p className="text-xs text-fg-3">
          Chưa có kết nối {k.label.toLowerCase()}.{' '}
          <Link to="/settings/providers" className={cn('sr-hit rounded-sm text-fg-1 underline underline-offset-2', focusRing)}>
            Thêm kết nối
          </Link>
        </p>
      )}
    </section>
  );
}

/**
 * Settings › Luồng: trần số việc chạy cùng lúc cho mỗi loại provider trên toàn app. Đổi là áp dụng ngay (gộp các lần
 * bấm liên tiếp thành một lần ghi), có Hoàn tác; số đang chạy/chờ cập nhật sống.
 */
export function SettingsConcurrency() {
  const viewer = useIsViewer();
  const qc = useQueryClient();
  const q = useConcurrency();
  const [pending, setPending] = useState<Caps>({});
  const pendingRef = useRef<Caps>({});
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const save = useMutation({
    mutationFn: async (v: { values: Caps; previous: Caps }) => setCaps(v.values),
    onSuccess: (data, v) => {
      qc.setQueryData(qk.concurrency, data);
      setPending((cur) => {
        const next = { ...cur };
        for (const [kind, n] of Object.entries(v.values)) if (next[kind as ConcurrencyKind] === n) delete next[kind as ConcurrencyKind];
        pendingRef.current = next;
        return next;
      });
      toast({
        tone: 'success',
        message: APPLIED_MESSAGE,
        undo: async () => {
          qc.setQueryData(qk.concurrency, await setCaps(v.previous));
        },
      });
    },
    onError: (e, v) => {
      setPending((cur) => {
        const next = { ...cur };
        for (const kind of Object.keys(v.values)) delete next[kind as ConcurrencyKind];
        pendingRef.current = next;
        return next;
      });
      toast({ tone: 'error', message: `Chưa đổi được số luồng: ${toApiError(e).detail}` });
    },
  });

  /** Ghi mọi giá trị đang chờ (một lần gọi). Gọi khi hết thời gian gộp, hoặc khi rời trang giữa chừng. */
  const flush = () => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    const values = pendingRef.current;
    const current = qc.getQueryData<ConcurrencyOverview>(qk.concurrency);
    if (!current) return;
    const previous: Caps = {};
    const changed: Caps = {};
    for (const [k, v] of Object.entries(values) as [ConcurrencyKind, number][]) {
      const was = current.kinds.find((x) => x.kind === k)?.cap;
      if (was == null || was === v) continue;
      previous[k] = was;
      changed[k] = v;
    }
    if (!Object.keys(changed).length) {
      pendingRef.current = {};
      setPending({});
      return;
    }
    save.mutate({ values: changed, previous });
  };
  const flushRef = useRef(flush);
  useEffect(() => {
    flushRef.current = flush;
  });
  useEffect(
    () => () => {
      if (timer.current) flushRef.current(); // rời trang trước khi hết thời gian gộp: vẫn ghi, không mất thay đổi
    },
    [],
  );

  const change = (kind: ConcurrencyKind, n: number) => {
    if (!q.data) return;
    const next = { ...pendingRef.current, [kind]: clampCap(n) };
    pendingRef.current = next;
    setPending(next);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => flushRef.current(), COMMIT_DELAY_MS);
  };

  const view = q.data ? withPending(q.data, pending) : null;

  return (
    <div className="grid gap-6">
      <SectionHeader
        title="Luồng"
        description="Số việc chạy cùng lúc cho mỗi loại provider, tính chung mọi kết nối cùng loại. Trần riêng của từng kết nối vẫn áp dụng bên trong. Đổi là việc mới dùng ngay; việc đang chạy không bị dừng."
      />
      {q.isError && !q.data ? (
        <ErrorState what="Không tải được số luồng" error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      ) : !view ? (
        <SkeletonZone label="Đang tải số luồng">
          <SkeletonRows count={6} />
        </SkeletonZone>
      ) : (
        <>
          {view.paused ? (
            <p role="status" className="sr-warnbox rounded-md px-4 py-2.5 text-sm">
              Đang tạm dừng nhận việc: trần vẫn lưu, nhưng việc mới chỉ chạy khi bạn tiếp tục ở trang{' '}
              <Link to="/jobs" className={cn('rounded-sm underline underline-offset-2', focusRing)}>
                Việc
              </Link>
              .
            </p>
          ) : null}
          <div className="overflow-hidden rounded-md border border-line-2 bg-bg-1">
            {view.kinds.map((k) => (
              <KindRow key={k.kind} k={k} disabled={viewer} onChange={(n) => change(k.kind, n)} />
            ))}
          </div>
          <p className="max-w-prose text-xs text-fg-3">
            Trần từ {CAP_MIN} đến {CAP_MAX}. Viết truyện và dịch lồng tiếng tính vào luồng LLM; dựng tập và xuất gói tính
            vào Dựng phim. Trần riêng của kết nối đặt trong Cài đặt › Kết nối.
          </p>
        </>
      )}
    </div>
  );
}
