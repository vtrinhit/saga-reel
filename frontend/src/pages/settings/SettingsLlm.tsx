import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowRight, CircleAlert, Hourglass, Pencil, Plus, Save, Trash } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import { useIsViewer } from '../../api/auth';
import { callOpLoose, hasOp, useOpsCatalog } from '../../api/catalog';
import { toApiError, type ApiError } from '../../api/client';
import { useConnections } from '../../api/hooks';
import type { Connection } from '../../api/types';
import { Button } from '../../components/ui/Button';
import { Drawer } from '../../components/ui/Drawer';
import { EmptyState } from '../../components/ui/EmptyState';
import { ErrorState } from '../../components/ui/ErrorState';
import { IconButton } from '../../components/ui/IconButton';
import { Input } from '../../components/ui/Input';
import { Select } from '../../components/ui/Select';
import { SkeletonRows, SkeletonZone } from '../../components/ui/Skeleton';
import { Textarea } from '../../components/ui/Textarea';
import {
  buildTaskUpdate,
  normalizeModels,
  normalizeTasks,
  paramsText,
  routeToDraft,
  type LlmRoute,
  type LlmTask,
  type RouteDraft,
} from '../../lib/llmRouting';
import { toast } from '../../stores/toast';
import { SectionHeader } from './SettingsLayout';

const tasksKey = ['llm', 'tasks'] as const;

/** Model của một kết nối LLM (`llm.models`, không tốn tiền); lỗi → cho gõ tay. */
function ModelPicker({
  id,
  connection,
  value,
  onChange,
  fallbackHint,
}: {
  id: string;
  connection: string;
  value: string;
  onChange: (v: string) => void;
  fallbackHint: string;
}) {
  const models = useQuery({
    queryKey: ['llm', 'models', connection],
    queryFn: async () => normalizeModels(await callOpLoose('llm.models', { connection })),
    enabled: !!connection,
    staleTime: 5 * 60_000,
    retry: false,
  });
  if (!connection) {
    return <Input id={id} label="Model" value="" disabled placeholder="Chọn kết nối trước" onChange={() => {}} />;
  }
  if (models.isError || (models.data && models.data.length === 0)) {
    const err = models.error ? toApiError(models.error) : null;
    return (
      <Input
        id={id}
        label="Model"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={fallbackHint}
        hint={err ? `Không lấy được danh sách model (${err.detail}) — gõ tên model.` : 'Kết nối không liệt kê model — gõ tên model.'}
        spellCheck={false}
        className="font-mono"
      />
    );
  }
  const options = [{ value: '', label: fallbackHint }, ...(models.data ?? []).map((m) => ({ value: m, label: m }))];
  if (value && !models.data?.includes(value)) options.push({ value, label: `${value} (không có trong danh sách)` });
  return (
    <Select
      id={id}
      label="Model"
      value={value}
      onChange={onChange}
      placeholder={models.isLoading ? 'Đang hỏi model…' : 'Chọn model…'}
      disabled={models.isLoading}
      options={options}
    />
  );
}

function RouteEditor({
  prefix,
  draft,
  onChange,
  connections,
  errors,
  allowDefault,
}: {
  prefix: string;
  draft: RouteDraft;
  onChange: (d: RouteDraft) => void;
  connections: Connection[];
  errors: Record<string, string>;
  allowDefault: boolean;
}) {
  const conn = connections.find((c) => c.name === draft.connection);
  return (
    <div className="grid gap-3">
      <Select
        id={`${prefix}-conn`}
        label="Kết nối"
        value={draft.connection}
        onChange={(v) => onChange({ ...draft, connection: v, model: v === draft.connection ? draft.model : '' })}
        error={errors[`${prefix}.connection`]}
        placeholder="Chọn kết nối LLM…"
        options={[
          ...(allowDefault ? [{ value: '', label: 'Mặc định (kết nối LLM đang bật đầu tiên)' }] : []),
          ...connections.map((c) => ({ value: c.name, label: c.name, description: c.enabled ? c.adapter : `${c.adapter} · đang tắt` })),
        ]}
      />
      <ModelPicker
        id={`${prefix}-model`}
        connection={draft.connection}
        value={draft.model}
        onChange={(v) => onChange({ ...draft, model: v })}
        fallbackHint={conn?.default_model ? `Mặc định của kết nối (${conn.default_model})` : 'Mặc định của kết nối'}
      />
      <Textarea
        id={`${prefix}-params`}
        label="Tham số (JSON, tuỳ chọn)"
        placeholder='{"temperature": 0.7, "max_tokens": 2000}'
        value={draft.params}
        onChange={(e) => onChange({ ...draft, params: e.target.value })}
        error={errors[`${prefix}.params`]}
        minRows={2}
        maxRows={8}
        spellCheck={false}
        className="font-mono text-sm md:text-sm"
      />
    </div>
  );
}

function TaskDrawer({ task, connections, onClose }: { task: LlmTask | null; connections: Connection[]; onClose: () => void }) {
  const qc = useQueryClient();
  const [route, setRoute] = useState<RouteDraft>({ connection: '', model: '', params: '' });
  const [fallbacks, setFallbacks] = useState<RouteDraft[]>([]);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [serverError, setServerError] = useState<ApiError | null>(null);

  useEffect(() => {
    if (!task) return;
    setRoute(routeToDraft(task.route));
    setFallbacks(task.fallbacks.map(routeToDraft));
    setErrors({});
    setServerError(null);
  }, [task]);

  const save = useMutation({
    mutationFn: async () => {
      const r = buildTaskUpdate(task!.key, route, fallbacks);
      if ('errors' in r) {
        setErrors(r.errors);
        return null;
      }
      setErrors({});
      return callOpLoose('llm.task_update', r.params);
    },
    onSuccess: (res) => {
      if (res == null) return;
      void qc.invalidateQueries({ queryKey: tasksKey });
      toast({ tone: 'success', message: `Đã lưu định tuyến “${task!.label}”.` });
      onClose();
    },
    onError: (e) => setServerError(toApiError(e)),
  });

  return (
    <Drawer
      open={task != null}
      onClose={onClose}
      title={task ? task.label : ''}
      footer={
        <>
          <Button onClick={onClose}>Huỷ</Button>
          <Button variant="primary" icon={Save} loading={save.isPending} onClick={() => save.mutate()}>
            Lưu
          </Button>
        </>
      }
    >
      {task ? (
        <div className="grid gap-5">
          <p className="text-xs text-fg-2">
            <span className="font-mono">{task.key}</span>
            {task.description ? ` · ${task.description}` : ''}
          </p>
          <section className="grid gap-3">
            <h3 className="text-sm font-semibold">Route chính</h3>
            <RouteEditor prefix="route" draft={route} onChange={setRoute} connections={connections} errors={errors} allowDefault />
          </section>
          <section className="grid gap-3 border-t border-line-1 pt-4">
            <div className="flex items-center justify-between gap-2">
              <h3 className="text-sm font-semibold">Dự phòng</h3>
              <Button
                variant="ghost"
                icon={Plus}
                disabled={fallbacks.length >= 10}
                onClick={() => setFallbacks((f) => [...f, { connection: '', model: '', params: '' }])}
              >
                Thêm dự phòng
              </Button>
            </div>
            {fallbacks.length === 0 ? (
              <p className="text-sm text-fg-3">Không có. Route chính lỗi thì việc LLM thất bại.</p>
            ) : (
              fallbacks.map((f, i) => (
                <div key={i} className="grid gap-2 border-t border-line-1 pt-3 first:border-t-0 first:pt-0">
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-fg-2">Dự phòng {i + 1}</span>
                    <IconButton
                      icon={Trash}
                      label={`Bỏ dự phòng ${i + 1}`}
                      onClick={() => setFallbacks((all) => all.filter((_, j) => j !== i))}
                    />
                  </div>
                  <RouteEditor
                    prefix={`fallbacks.${i}`}
                    draft={f}
                    onChange={(d) => setFallbacks((all) => all.map((x, j) => (j === i ? d : x)))}
                    connections={connections}
                    errors={errors}
                    allowDefault={false}
                  />
                </div>
              ))
            )}
          </section>
          {serverError ? (
            <p role="alert" className="text-sm text-state-failed">
              {serverError.detail}
              {serverError.hint ? ` ${serverError.hint}` : ''}
            </p>
          ) : null}
        </div>
      ) : null}
    </Drawer>
  );
}

function RouteCell({ r, empty }: { r: LlmRoute; empty: string }) {
  return (
    <span className="grid min-w-0">
      <span className="truncate text-sm">{r.connection ?? <span className="text-fg-3">{empty}</span>}</span>
      <span className="truncate font-mono text-xs text-fg-2">{r.model ?? 'model mặc định'}</span>
    </span>
  );
}

function NoLlmGuidance() {
  const navigate = useNavigate();
  return (
    <div role="status" className="sr-warnbox mb-4 flex flex-wrap items-start gap-3 rounded-md px-4 py-3">
      <CircleAlert className="mt-0.5 h-5 w-5 flex-none text-warn" strokeWidth={1.75} aria-hidden />
      <div className="grid min-w-0 flex-1 gap-1">
        <p className="text-sm font-semibold">Chưa có kết nối LLM nào đang bật.</p>
        <p className="text-sm text-fg-1">
          Việc dùng LLM (viết kịch bản, tách cảnh…) sẽ báo <span className="font-mono text-xs">llm_not_configured</span>. Thêm
          kết nối LLM, hoặc tự viết / nhập file kịch bản, hoặc để agent ngoài làm qua MCP.
        </p>
      </div>
      <Button onClick={() => navigate('/settings/providers')}>Thêm kết nối LLM</Button>
    </div>
  );
}

/**
 * Settings › Định tuyến LLM: công việc → kết nối/model/tham số/dự phòng. Ops `llm.*` (Task 13) có thể chưa có trên
 * máy chủ này → "Chưa có", không chặn phần còn lại.
 */
export function SettingsLlm() {
  const catalog = useOpsCatalog();
  const available = hasOp(catalog.data, 'llm.task_list');
  const conns = useConnections();
  const tasks = useQuery({
    queryKey: tasksKey,
    queryFn: async () => normalizeTasks(await callOpLoose('llm.task_list', {})),
    enabled: available,
  });
  const [editing, setEditing] = useState<LlmTask | null>(null);
  const viewer = useIsViewer();
  const llmConns = (conns.data ?? []).filter((c) => c.kind === 'llm');
  const noLlm = conns.data != null && !llmConns.some((c) => c.enabled);

  const header = (
    <SectionHeader
      title="Định tuyến LLM"
      description="Mỗi công việc LLM chạy trên kết nối và model nào, với tham số gì, và dự phòng khi lỗi."
    />
  );

  if (catalog.isError) {
    return (
      <>
        {header}
        <ErrorState what="Không tải được danh mục thao tác của máy chủ" error={catalog.error} onRetry={() => void catalog.refetch()} />
      </>
    );
  }
  if (!catalog.data) {
    return (
      <>
        {header}
        <SkeletonZone label="Đang tải">
          <SkeletonRows count={4} />
        </SkeletonZone>
      </>
    );
  }
  if (!available) {
    return (
      <>
        {header}
        <div className="rounded-md border border-line-2 bg-bg-1 p-6">
          <EmptyState
            icon={Hourglass}
            title="Chưa có"
            description="Máy chủ này chưa có định tuyến LLM theo công việc. Mục này tự bật khi máy chủ được cập nhật."
          />
        </div>
      </>
    );
  }

  return (
    <>
      {header}
      {noLlm ? <NoLlmGuidance /> : null}
      {tasks.isError ? (
        <ErrorState what="Không tải được danh sách công việc LLM" error={tasks.error} onRetry={() => void tasks.refetch()} />
      ) : !tasks.data ? (
        <SkeletonZone label="Đang tải công việc LLM">
          <SkeletonRows count={5} />
        </SkeletonZone>
      ) : tasks.data.length === 0 ? (
        <p className="text-sm text-fg-3">Chưa có công việc LLM nào.</p>
      ) : (
        <div className="overflow-hidden rounded-md border border-line-2 bg-bg-1">
          <div
            aria-hidden
            className="hidden grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_auto] gap-4 border-b border-line-1 bg-bg-1 px-4 py-2 text-xs text-fg-2 xl:grid"
          >
            <span>Công việc</span>
            <span>Kết nối · model</span>
            <span>Tham số</span>
            <span>Dự phòng</span>
            <span className="w-[var(--h-control)]" />
          </div>
          <ul>
            {tasks.data.map((t) => (
              <li
                key={t.key}
                className="grid min-w-0 gap-x-4 gap-y-1.5 border-b border-line-1 px-4 py-3 last:border-b-0 xl:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_auto] xl:items-center"
              >
                <span className="grid min-w-0">
                  <span className="truncate text-sm font-medium">{t.label}</span>
                  <span className="truncate font-mono text-xs text-fg-3">{t.key}</span>
                </span>
                <RouteCell r={t.route} empty="Mặc định" />
                <span className="min-w-0 truncate font-mono text-xs text-fg-2">
                  {Object.keys(t.route.params).length ? paramsText(t.route.params) : <span className="font-sans text-fg-3">Không có tham số</span>}
                </span>
                <span className="flex min-w-0 flex-wrap items-center gap-1 text-xs text-fg-2">
                  {t.fallbacks.length === 0 ? (
                    <span className="text-fg-3">Không có dự phòng</span>
                  ) : (
                    t.fallbacks.map((f, i) => (
                      <span key={i} className="inline-flex items-center gap-1">
                        {i > 0 ? <ArrowRight className="h-3 w-3" aria-label="rồi" /> : null}
                        <span className="truncate">{f.connection}</span>
                      </span>
                    ))
                  )}
                </span>
                <span className="justify-self-start xl:justify-self-end">
                  {viewer ? null : <IconButton icon={Pencil} label={`Sửa định tuyến ${t.label}`} onClick={() => setEditing(t)} />}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
      <TaskDrawer task={editing} connections={llmConns} onClose={() => setEditing(null)} />
    </>
  );
}
