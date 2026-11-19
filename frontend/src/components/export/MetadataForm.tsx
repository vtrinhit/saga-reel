/**
 * Metadata của ô đang chọn: tiêu đề, mô tả/caption, hashtag — đếm ký tự theo giới hạn preset nền tảng (UTF-16 khi
 * nền tảng đo UTF-16), nhãn nội dung AI bắt buộc (khoá bật). Gói đã xuất → hiện metadata thật của gói.
 * Server chưa nhận metadata sửa tay khi xuất: "Kiểm tra" gửi `export.check(metadata=…)` để đối chiếu giới hạn.
 */
import { Bot, Lock } from 'lucide-react';
import { useEffect, useState } from 'react';
import { cn } from '../../lib/cn';
import { countBytes, countChars, hashtagsOf, langLabel, type MetaLimits } from '../../lib/exportMatrix';
import type { ExportRow } from '../../lib/studioData';
import { Button } from '../ui/Button';
import { Input } from '../ui/Input';
import { Textarea } from '../ui/Textarea';

export interface MetaDraft {
  title: string;
  description: string;
}

function Count({ n, max, unit = '' }: { n: number; max: number | null; unit?: string }) {
  const over = max != null && n > max;
  return (
    <span className={cn('font-mono text-xs tabular-nums', over ? 'text-state-failed' : 'text-fg-3')}>
      {n}
      {max != null ? ` / ${max}` : ''}
      {unit}
    </span>
  );
}

export function MetadataForm({ platformName, lang, limits, pkg, checking, readOnly, onCheck }: {
  platformName: string;
  lang: string;
  limits: MetaLimits;
  pkg: ExportRow | null;
  checking?: boolean;
  readOnly?: boolean;
  onCheck: (draft: MetaDraft & { hashtags: string[] }) => void;
}) {
  const md = (pkg?.metadata ?? {}) as Record<string, unknown>;
  const fromPkg = (): MetaDraft => ({
    title: String(md.title ?? ''),
    description: [md.description, Array.isArray(md.hashtags) ? (md.hashtags as string[]).join(' ') : ''].filter(Boolean).join('\n\n'),
  });
  const [draft, setDraft] = useState<MetaDraft>(fromPkg);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => setDraft(fromPkg()), [pkg?.id]);
  const tags = hashtagsOf(draft.description);
  const source = md.source ? String(md.source) : null;
  return (
    <section aria-labelledby="md-title" className="rounded-md border border-line-2 bg-bg-1">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line-1 px-3 py-2">
        <h2 id="md-title" className="text-sm font-semibold">
          Metadata · {platformName} · {langLabel(lang)}
        </h2>
        {source ? (
          <span className="inline-flex items-center gap-1 rounded-sm bg-bg-3 px-2 text-xs text-fg-2">
            <Bot className="size-3" aria-hidden />
            {source === 'template' ? 'viết theo mẫu' : `viết bởi ${source}`}
          </span>
        ) : null}
      </div>
      <div className="grid gap-3 p-3">
        {!pkg ? (
          <p className="text-sm text-fg-2">
            Metadata được viết lúc xuất gói (LLM hoặc mẫu). Bạn có thể nháp ở đây để kiểm giới hạn của {platformName}.
          </p>
        ) : null}
        {limits.titleMax != null ? (
          <Input
            id="md-title-in"
            label="Tiêu đề"
            labelExtra={<Count n={countChars(draft.title)} max={limits.titleMax} />}
            value={draft.title}
            readOnly={readOnly}
            onChange={(e) => setDraft({ ...draft, title: e.target.value })}
          />
        ) : null}
        <Textarea
          id="md-desc"
          label={limits.titleMax == null ? 'Caption' : 'Mô tả'}
          labelExtra={
            <span className="flex gap-2">
              <Count n={countChars(draft.description, limits.captionUtf16)} max={limits.captionMax} />
              {limits.captionBytes != null ? <Count n={countBytes(draft.description)} max={limits.captionBytes} unit=" byte" /> : null}
            </span>
          }
          hint={
            limits.hashtagMax != null ? (
              <span className={tags.length > limits.hashtagMax ? 'text-state-failed' : undefined}>
                {tags.length} hashtag (tối đa {limits.hashtagMax})
              </span>
            ) : undefined
          }
          value={draft.description}
          readOnly={readOnly}
          minRows={3}
          maxRows={10}
          onChange={(e) => setDraft({ ...draft, description: e.target.value })}
        />
        <label className="flex items-center gap-2 text-sm text-fg-2">
          <input type="checkbox" checked readOnly disabled className="size-4 accent-[rgb(var(--c-accent))]" />
          <Lock className="size-3.5" aria-hidden />
          Gắn nhãn nội dung do AI tạo{limits.aiLabelRequired ? ' (bắt buộc)' : ''}
        </label>
        <div className="flex justify-end">
          <Button loading={checking} onClick={() => onCheck({ ...draft, hashtags: tags })}>
            Kiểm tra metadata
          </Button>
        </div>
      </div>
    </section>
  );
}
