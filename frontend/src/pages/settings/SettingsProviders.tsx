import { useMutation, useQueryClient } from '@tanstack/react-query';
import { FlaskConical, Pencil, Plug, Plus, RefreshCw, Trash } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useIsViewer } from '../../api/auth';
import { toApiError } from '../../api/client';
import { applyHealthEvent, qk, useAdapters, useConnections } from '../../api/hooks';
import { callOp } from '../../api/ops';
import type { Adapter, Connection, DeletePreview, ProviderKind } from '../../api/types';
import { Button } from '../../components/ui/Button';
import { ConfirmDelete } from '../../components/ui/ConfirmDelete';
import { EmptyState } from '../../components/ui/EmptyState';
import { ErrorState } from '../../components/ui/ErrorState';
import { IconButton } from '../../components/ui/IconButton';
import { Modal } from '../../components/ui/Modal';
import { SkeletonRows, SkeletonZone } from '../../components/ui/Skeleton';
import { StatusPill } from '../../components/ui/StatusPill';
import { formatCount } from '../../lib/format';
import { relativeFromServer } from '../../lib/time';
import { toast } from '../../stores/toast';
import { ProviderForm } from './ProviderForm';
import { healthView, KIND_LABEL, KIND_ORDER } from './providerText';
import { SectionHeader } from './SettingsLayout';
import { TryDialog } from './TryDialog';

/** Đồng hồ cho chữ "kiểm tra 12 giây trước" (cập nhật mỗi 10 giây). */
function useNow(ms = 10_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(t);
  }, [ms]);
  return now;
}

function ConnectionRow({
  conn,
  adapter,
  now,
  onEdit,
  onTry,
  readOnly,
}: {
  readOnly?: boolean;
  conn: Connection;
  adapter?: Adapter;
  now: number;
  onEdit: () => void;
  onTry: () => void;
}) {
  const qc = useQueryClient();
  const hv = healthView(conn);
  const checkedAt = conn.health?.checked_at;

  const check = useMutation({
    mutationFn: async () => (await callOp('provider.check', { ids: [conn.id] })) as { items: { id: number; name: string; health: Connection['health'] }[] },
    // Sự kiện provider.health cũng cập nhật; áp kết quả ngay phòng khi luồng sự kiện đang đứt.
    onSuccess: (r) => r.items.forEach((it) => applyHealthEvent(qc, it)),
    onError: (e) => {
      const err = toApiError(e);
      toast({ tone: 'error', message: `Không kiểm tra được ${conn.name}: ${err.detail}` });
    },
  });

  // Xoá: delete_preview trước; có tác động (việc đang chờ, cài đặt trỏ tới tên này) → hộp nói rõ rồi mới xoá.
  const [impact, setImpact] = useState<DeletePreview | null>(null);
  const [impactNote, setImpactNote] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const hasImpact = (p: DeletePreview) => p.impact.active_jobs > 0 || p.impact.used_by_settings.length > 0;
  const preview = async () => (await callOp('provider.delete_preview', { id: conn.id })) as DeletePreview;

  const doDelete = async (p: DeletePreview) => {
    setDeleting(true);
    try {
      await callOp('provider.delete', { id: conn.id, confirm_token: p.confirm_token });
      setImpact(null);
      await qc.invalidateQueries({ queryKey: qk.connections });
      toast({ message: `Đã xoá kết nối ${conn.name}.` });
    } catch (e) {
      const err = toApiError(e);
      if (err.code === 'confirm_required') {
        // Tác động đổi giữa lúc xem và lúc xoá (hoặc token hết hạn): xem lại, hỏi lại.
        try {
          setImpact(await preview());
          setImpactNote('Tác động đã thay đổi kể từ lúc xem — kiểm tra lại trước khi xoá.');
        } catch (e2) {
          toast({ tone: 'error', message: `Không xoá được ${conn.name}: ${toApiError(e2).detail}` });
        }
      } else {
        toast({ tone: 'error', message: `Không xoá được ${conn.name}: ${err.detail}` });
      }
    } finally {
      setDeleting(false);
    }
  };

  const remove = async () => {
    try {
      const p = await preview();
      if (hasImpact(p)) {
        setImpactNote(null);
        setImpact(p);
      } else await doDelete(p);
    } catch (e) {
      toast({ tone: 'error', message: `Không xem được tác động khi xoá ${conn.name}: ${toApiError(e).detail}` });
    }
  };

  return (
    <li className="grid min-w-0 gap-x-4 gap-y-2 border-b border-line-1 px-3 py-3 last:border-b-0 md:grid-cols-[minmax(0,1fr)_auto] md:items-center md:px-4">
      <div className="grid min-w-0 gap-1">
        <div className="flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1">
          <span className="min-w-0 truncate text-sm font-semibold">{conn.name}</span>
          <StatusPill status={hv.pill} label={hv.label} title={conn.health?.detail || undefined} />
          {checkedAt ? (
            <span className="text-xs text-fg-3">kiểm tra {relativeFromServer(checkedAt, now)}</span>
          ) : null}
        </div>
        <p className="min-w-0 truncate text-xs text-fg-2">
          {adapter?.label ?? conn.adapter}
          {conn.base_url ? <> · <span className="font-mono">{conn.base_url}</span></> : null}
          {conn.has_api_key ? <> · khoá <span className="font-mono">{conn.api_key}</span></> : null}
        </p>
        {conn.health?.detail && conn.health.status !== 'alive' ? (
          <p className="text-xs text-fg-2">{conn.health.detail}</p>
        ) : null}
      </div>
      {readOnly ? null : <div className="flex flex-wrap items-center gap-2">
        <Button icon={RefreshCw} loading={check.isPending} onClick={() => check.mutate()}>
          Kiểm tra
        </Button>
        <Button icon={FlaskConical} onClick={onTry}>
          Thử
        </Button>
        <IconButton icon={Pencil} label={`Sửa ${conn.name}`} onClick={onEdit} />
        <ConfirmDelete what={`kết nối ${conn.name}`} onConfirm={remove} />
      </div>}
      <Modal
        open={impact != null}
        role="alertdialog"
        title={`Xoá kết nối ${conn.name}?`}
        onClose={deleting ? undefined : () => setImpact(null)}
        actions={
          <>
            <Button onClick={() => setImpact(null)} disabled={deleting}>
              Giữ lại
            </Button>
            <Button variant="danger" armed icon={Trash} loading={deleting} onClick={() => impact && void doDelete(impact)}>
              Xoá kết nối
            </Button>
          </>
        }
      >
        {impact ? (
          <ul className="grid list-disc gap-1.5 pl-5">
            {impact.impact.active_jobs > 0 ? (
              <li>
                {formatCount(impact.impact.active_jobs)} việc đang chờ hoặc đang chạy trên kết nối này sẽ thất bại.
              </li>
            ) : null}
            {impact.impact.used_by_settings.length ? (
              <li>
                Cài đặt đang trỏ tới tên này:{' '}
                <span className="font-mono text-xs">{impact.impact.used_by_settings.join(', ')}</span> — việc mới dùng các
                cài đặt đó sẽ báo thiếu kết nối cho tới khi bạn chọn kết nối khác.
              </li>
            ) : null}
            {impactNote ? <li className="text-warn">{impactNote}</li> : null}
          </ul>
        ) : null}
      </Modal>
    </li>
  );
}

/** Settings › Kết nối: kết nối provider theo loại, sức khoẻ sống, kiểm tra, thử (ước tính trước), thêm/sửa/xoá. */
export function SettingsProviders() {
  const adapters = useAdapters();
  const conns = useConnections();
  const now = useNow();
  const [form, setForm] = useState<{ conn?: Connection; kind?: ProviderKind } | null>(null);
  const [trying, setTrying] = useState<Connection | null>(null);
  const qc = useQueryClient();
  const viewer = useIsViewer();

  const checkAll = useMutation({
    mutationFn: async () => (await callOp('provider.check', {})) as { items: { id: number; name: string; health: Connection['health'] }[] },
    onSuccess: (r) => {
      r.items.forEach((it) => applyHealthEvent(qc, it));
      toast({ tone: 'success', message: `Đã kiểm tra ${formatCount(r.items.length)} kết nối đang bật.` });
    },
    onError: (e) => toast({ tone: 'error', message: `Không kiểm tra được: ${toApiError(e).detail}` }),
  });

  const adapterOf = (id: string) => adapters.data?.find((a) => a.id === id);
  const header = (
    <SectionHeader
      title="Kết nối"
      description="Provider tạo ảnh, video, giọng đọc và LLM. Khoá API chỉ hiện dạng che; app không tự gọi provider khi khởi động."
      actions={
        viewer ? undefined : <>
          <Button
            icon={RefreshCw}
            loading={checkAll.isPending}
            disabled={!conns.data?.some((c) => c.enabled)}
            onClick={() => checkAll.mutate()}
          >
            Kiểm tra tất cả
          </Button>
          <Button variant="primary" icon={Plus} disabled={!adapters.data} onClick={() => setForm({})}>
            Thêm kết nối
          </Button>
        </>
      }
    />
  );

  if (conns.isError || adapters.isError) {
    return (
      <>
        {header}
        <ErrorState
          what="Không tải được danh sách kết nối"
          error={conns.error ?? adapters.error}
          onRetry={() => {
            void conns.refetch();
            void adapters.refetch();
          }}
          retrying={conns.isFetching}
        />
      </>
    );
  }

  return (
    <>
      {header}
      {!conns.data || !adapters.data ? (
        <SkeletonZone label="Đang tải kết nối">
          <SkeletonRows count={4} />
        </SkeletonZone>
      ) : conns.data.length === 0 ? (
        <div className="rounded-md border border-line-2 bg-bg-1 p-6">
          <EmptyState
            icon={Plug}
            title="Chưa có kết nối nào."
            description="Thêm kết nối provider để tạo ảnh, video, giọng đọc. Muốn thử giao diện mà không tốn tiền: chọn provider giả (fake)."
            action={
              viewer ? undefined : (
                <Button variant="primary" icon={Plus} onClick={() => setForm({})}>
                  Thêm kết nối
                </Button>
              )
            }
          />
        </div>
      ) : (
        <div className="grid gap-6">
          {KIND_ORDER.map((kind) => {
            const items = conns.data!.filter((c) => c.kind === kind);
            return (
              <section key={kind} aria-labelledby={`kind-${kind}`} className="grid gap-2">
                <div className="flex items-center justify-between gap-2">
                  <h3 id={`kind-${kind}`} className="text-sm font-semibold text-fg-2">
                    {KIND_LABEL[kind]} <span className="font-normal text-fg-3 tnum">· {formatCount(items.length)}</span>
                  </h3>
                  {viewer ? null : (
                    <Button variant="ghost" icon={Plus} onClick={() => setForm({ kind })}>
                      Thêm
                    </Button>
                  )}
                </div>
                {items.length ? (
                  <ul className="overflow-hidden rounded-md border border-line-2 bg-bg-1">
                    {items.map((c) => (
                      <ConnectionRow
                        key={c.id}
                        conn={c}
                        adapter={adapterOf(c.adapter)}
                        now={now}
                        onEdit={() => setForm({ conn: c })}
                        onTry={() => setTrying(c)}
                        readOnly={viewer}
                      />
                    ))}
                  </ul>
                ) : (
                  <p className="rounded-md border border-dashed border-line-2 px-4 py-3 text-sm text-fg-3">
                    Chưa có kết nối {KIND_LABEL[kind]}.
                  </p>
                )}
              </section>
            );
          })}
        </div>
      )}

      {adapters.data ? (
        <ProviderForm
          open={form != null}
          onClose={() => setForm(null)}
          adapters={adapters.data}
          conn={form?.conn ?? null}
          kind={form?.kind ?? null}
        />
      ) : null}
      {trying ? (
        <TryDialog
          conn={conns.data?.find((c) => c.id === trying.id) ?? trying}
          adapter={adapterOf(trying.adapter)}
          onClose={() => setTrying(null)}
        />
      ) : null}
    </>
  );
}
