import { Eye, FilePlus2, Save } from 'lucide-react';
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError } from '../../api/client';
import { callOp } from '../../api/ops';
import { errText } from '../../components/library/useLibrary';
import { Button } from '../../components/ui/Button';
import { ErrorState } from '../../components/ui/ErrorState';
import { SkeletonRows } from '../../components/ui/Skeleton';
import { Textarea } from '../../components/ui/Textarea';
import { cn, focusRing } from '../../lib/cn';
import {
  groupContracts,
  previewText,
  type PromptCheck,
  type PromptContract,
  type PromptItem,
  type PromptList,
  type PromptPreview,
} from '../../lib/prompts';
import { toast } from '../../stores/toast';

const keys = {
  contracts: ['prompts', 'contracts'] as const,
  list: (pid: number) => ['prompts', 'list', pid] as const,
  one: (pid: number, key: string) => ['prompts', 'get', pid, key] as const,
};

function useContracts() {
  return useQuery({
    queryKey: keys.contracts,
    queryFn: async () => ((await callOp('prompt.contracts', {})) as unknown as { items: PromptContract[] }).items,
    staleTime: 30 * 60_000,
  });
}

/** Trình sửa một khoá: thân Jinja + gợi ý biến từ hợp đồng + xem trước với dữ liệu thật. */
function PromptEditor({ projectId, contract, exists }: { projectId: number; contract: PromptContract; exists: boolean }) {
  const qc = useQueryClient();
  const one = useQuery({
    queryKey: keys.one(projectId, contract.key),
    queryFn: async () => (await callOp('prompt.get', { project_id: projectId, key: contract.key })) as unknown as PromptItem,
    enabled: exists,
  });
  const [draft, setDraft] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [preview, setPreview] = useState<PromptPreview | null>(null);
  const saved = one.data?.body ?? '';
  const body = draft ?? saved;
  const dirty = draft != null && draft !== saved;

  const save = useMutation({
    mutationFn: async () =>
      (await callOp('prompt.set', {
        project_id: projectId,
        key: contract.key,
        body,
        expected_updated_at: one.data?.updated_at ?? null,
      })) as unknown as PromptItem & { warnings: string[] },
    onSuccess: (r) => {
      setWarnings(r.warnings ?? []);
      setDraft(null);
      void qc.invalidateQueries({ queryKey: ['prompts'] });
      toast({ message: `Đã lưu prompt ${contract.label}.`, tone: 'success' });
    },
    onError: (e) => {
      toast({ message: errText(e, 'Không lưu được prompt.'), tone: 'error' });
      if (e instanceof ApiError && e.code === 'stale_write') void qc.invalidateQueries({ queryKey: ['prompts'] });
    },
  });
  const run = useMutation({
    mutationFn: async () =>
      (await callOp('prompt.preview', { project_id: projectId, key: contract.key, body })) as unknown as PromptPreview,
    onSuccess: (r) => setPreview(r),
    onError: (e) => {
      setPreview(null);
      toast({ message: errText(e, 'Không xem trước được.'), tone: 'error' });
    },
  });

  if (exists && one.isError) {
    return <ErrorState what="Không tải được prompt" error={one.error} onRetry={() => void one.refetch()} />;
  }
  if (exists && one.isPending) return <SkeletonRows count={4} />;

  return (
    <div className="grid min-w-0 content-start gap-3">
      <div className="grid gap-1">
        <h3 className="text-md font-semibold">{contract.label}</h3>
        <p className="max-w-[72ch] text-sm text-fg-2">{contract.purpose}</p>
        <p className="text-xs text-fg-3">
          Khoá <code className="font-mono">{contract.key}</code>
          {contract.used_by.length ? `, dùng bởi ${contract.used_by.join(', ')}` : ''}
        </p>
      </div>
      <Textarea
        id={`prompt-${contract.key}`}
        label="Nội dung (Jinja)"
        value={body}
        onChange={(e) => setDraft(e.target.value)}
        minRows={10}
        maxRows={32}
        showCount
        spellCheck={false}
        placeholder={contract.kind === 'llm' ? '<!-- role: system -->\n…\n<!-- role: user -->\n{{ input }}' : '{{ prompt }}'}
        className="font-mono text-sm md:text-sm"
      />
      {warnings.length ? (
        <ul className="grid gap-1 text-sm text-warn" aria-live="polite">
          {warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      ) : null}
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="primary" icon={Save} loading={save.isPending} disabled={!dirty || !body.trim()} onClick={() => save.mutate()}>
          Lưu prompt
        </Button>
        <Button icon={Eye} loading={run.isPending} disabled={!body.trim()} onClick={() => run.mutate()}>
          Xem trước
        </Button>
        {dirty ? (
          <Button variant="ghost" onClick={() => setDraft(null)}>
            Bỏ thay đổi
          </Button>
        ) : null}
      </div>
      <details className="rounded-md border border-line-1 px-3 py-2">
        <summary className="cursor-pointer text-sm font-medium">Biến dùng được ({contract.variables.length})</summary>
        <dl className="mt-2 grid gap-x-4 gap-y-1 text-sm sm:grid-cols-[max-content_minmax(0,1fr)]">
          {contract.variables.map((v) => (
            <div key={v.name} className="contents">
              <dt className="font-mono text-fg-1">
                {`{{ ${v.name} }}`}
                {v.required ? null : <span className="text-fg-3"> (tuỳ chọn)</span>}
              </dt>
              <dd className="text-fg-2">{v.description}</dd>
            </div>
          ))}
        </dl>
        {contract.output ? <p className="mt-2 text-xs text-fg-3">Đầu ra: {contract.output}</p> : null}
        {contract.output_format ? (
          <>
            <p className="mt-2 text-xs text-fg-3">
              Khối định dạng dưới đây do tool tự nối vào khi chạy bằng LLM của tool — prompt chỉ cần phần dặn dò sáng tạo.
            </p>
            <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded-sm bg-bg-2 p-2 font-mono text-xs text-fg-2">
              {contract.output_format}
            </pre>
          </>
        ) : null}
      </details>
      {preview ? (
        <section aria-label="Kết quả xem trước" className="grid gap-1">
          <h4 className="text-sm font-medium">Xem trước{preview.clip_id ? ` (clip #${preview.clip_id})` : ''}</h4>
          <pre className="max-h-[50vh] overflow-auto whitespace-pre-wrap rounded-md border border-line-1 bg-bg-2 p-3 font-mono text-xs text-fg-1">
            {previewText(preview)}
          </pre>
          {preview.warnings?.length ? (
            <ul className="grid gap-1 text-xs text-fg-2">
              {preview.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}

/**
 * Bộ prompt của dự án (Cài đặt dự án → Prompt): danh sách khoá theo nhóm của hợp đồng (Ráp prompt · AI viết · Kiểm tra
 * & khác), khoá còn thiếu, trình sửa + xem trước. Prompt là dữ liệu của dự án — sửa ở đây không đụng dự án khác.
 */
export function ProjectPrompts({ projectId }: { projectId: number }) {
  const contracts = useContracts();
  const list = useQuery({
    queryKey: keys.list(projectId),
    queryFn: async () => (await callOp('prompt.list', { project_id: projectId })) as unknown as PromptList,
  });
  // Kiểm tra tĩnh (không LLM, chỉ cảnh báo): cú pháp, biến lạ/chưa dùng, bước sẽ thiếu prompt.
  const check = useQuery({
    queryKey: ['prompts', 'check', projectId],
    queryFn: async () => (await callOp('prompt.check', { project_id: projectId })) as unknown as PromptCheck,
  });
  const [picked, setPicked] = useState<string | null>(null);
  const have = new Set((list.data?.items ?? []).map((x) => x.key));
  const groups = groupContracts(contracts.data ?? []);
  const current = contracts.data?.find((c) => c.key === picked) ?? contracts.data?.find((c) => have.has(c.key)) ?? contracts.data?.[0];
  const missing = list.data?.missing ?? [];
  const otherWarnings = (check.data?.warnings ?? []).filter((w) => w.code !== 'prompt_missing');

  return (
    <section aria-labelledby="ps-prompts" className="grid gap-4 border-t border-line-1 py-6">
      <div className="grid gap-1">
        <h2 id="ps-prompts" className="text-md font-semibold">
          Prompt
        </h2>
        <p className="max-w-[72ch] text-sm text-fg-2">
          Prompt là dữ liệu của dự án này: ráp prompt ảnh/video/sheet và lời dặn cho AI viết truyện. Sửa ở đây chỉ ảnh hưởng
          dự án này; dự án mới nhân bản từ dự án mẫu mang theo bản sao.
        </p>
      </div>
      {contracts.isError || list.isError ? (
        <ErrorState
          what="Không tải được prompt"
          error={contracts.error ?? list.error}
          onRetry={() => {
            void contracts.refetch();
            void list.refetch();
          }}
        />
      ) : contracts.isPending || list.isPending ? (
        <SkeletonRows count={6} />
      ) : (
        <>
          {missing.length ? (
            <div role="status" className="grid gap-1 rounded-md border border-line-2 bg-bg-1 px-3 py-2 text-sm">
              <p className="font-medium">Còn thiếu {missing.length} prompt — bước dùng chúng sẽ báo thiếu prompt.</p>
              <p className="text-fg-2">
                Viết từng khoá bên dưới, hoặc nhờ agent AI viết trọn bộ qua MCP: đọc <code className="font-mono">prompt.contracts</code>,
                rồi <code className="font-mono">prompt.set</code> từng khoá và <code className="font-mono">prompt.preview</code> để thử.
              </p>
            </div>
          ) : null}
          {otherWarnings.length ? (
            <div role="status" className="grid gap-1 rounded-md border border-line-2 bg-bg-1 px-3 py-2 text-sm">
              <p className="font-medium">Kiểm tra prompt: {otherWarnings.length} cảnh báo (không chặn việc chạy)</p>
              <ul className="grid gap-0.5 text-fg-2">
                {otherWarnings.map((w) => (
                  <li key={`${w.key}:${w.message}`}>
                    <button type="button" className={cn('text-left underline-offset-2 hover:underline', focusRing)} onClick={() => setPicked(w.key)}>
                      <code className="font-mono">{w.key}</code>: {w.message}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          <div className="grid gap-6 lg:grid-cols-[240px_minmax(0,1fr)]">
            <nav aria-label="Khoá prompt" className="grid content-start gap-4">
              {groups.map((g) => (
                <div key={g.group} className="grid gap-1">
                  <h3 className="text-xs font-medium text-fg-3">{g.label}</h3>
                  <ul className="grid gap-0.5">
                    {g.items.map((c) => {
                      const on = current?.key === c.key;
                      const ok = have.has(c.key);
                      return (
                        <li key={c.key}>
                          <button
                            type="button"
                            aria-current={on ? 'true' : undefined}
                            onClick={() => setPicked(c.key)}
                            className={cn(
                              'flex w-full min-w-0 items-center gap-2 rounded-sm px-2 py-1 text-left text-sm',
                              on ? 'bg-bg-3 text-fg-1' : 'text-fg-2 hover:bg-bg-2 hover:text-fg-1',
                              focusRing,
                            )}
                          >
                            <span className="min-w-0 flex-1 truncate">{c.label}</span>
                            {ok ? null : (
                              <span className="flex shrink-0 items-center gap-1 text-xs text-warn">
                                <FilePlus2 className="size-3.5" aria-hidden />
                                thiếu
                              </span>
                            )}
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              ))}
            </nav>
            {current ? (
              <PromptEditor key={current.key} projectId={projectId} contract={current} exists={have.has(current.key)} />
            ) : null}
          </div>
        </>
      )}
    </section>
  );
}
