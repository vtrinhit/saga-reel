/**
 * Metadata của ô đang chọn (tập × nền tảng × ngôn ngữ): điền sẵn bản server sẽ sinh ra (`export.check` → `metadata`),
 * sửa tiêu đề / mô tả / hashtag, đếm theo giới hạn preset nền tảng (mô tả + hashtag như `checks.full_caption`; UTF-16
 * khi nền tảng đo UTF-16), nhãn nội dung AI bắt buộc (khoá bật). "Dùng khi xuất" lưu bản sửa cho đúng ô này — lần
 * "Xuất" kế tiếp gửi `export.create(metadata=…)` chỉ với các khoá đã đổi. "Kiểm tra" gửi `export.check(metadata=…)`.
 */
import { Bot, Lock, RotateCcw } from 'lucide-react';
import { useEffect, useState } from 'react';
import { cn } from '../../lib/cn';
import {
  countBytes,
  countChars,
  draftFrom,
  fullCaption,
  langLabel,
  metaOverride,
  metaProblems,
  parseHashtags,
  type GeneratedMeta,
  type MetaDraft,
  type MetaLimits,
  type MetaOverride,
} from '../../lib/exportMatrix';
import type { ExportRow } from '../../lib/studioData';
import { Button } from '../ui/Button';
import { Input } from '../ui/Input';
import { Textarea } from '../ui/Textarea';

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

/** Áp bản ghi đè lên bản sinh ra (để điền form khi ô đã có bản sửa). */
function applyOverride(base: GeneratedMeta | null | undefined, o: MetaOverride | null | undefined): GeneratedMeta | null {
  if (!o) return base ?? null;
  return { ...(base ?? {}), ...o };
}

export function MetadataForm({ platformName, lang, limits, generated, generating, pkg, override, checking, readOnly, onCheck, onSave }: {
  platformName: string;
  lang: string;
  limits: MetaLimits;
  /** Metadata server sẽ sinh ra khi xuất ô này (`export.check`). */
  generated: GeneratedMeta | null;
  generating?: boolean;
  /** Gói gần nhất của ô (nếu đã xuất). */
  pkg: ExportRow | null;
  /** Bản sửa đã lưu cho ô (áp dụng ở lần xuất kế tiếp). */
  override: MetaOverride | null;
  checking?: boolean;
  readOnly?: boolean;
  onCheck: (metadata: GeneratedMeta) => void;
  onSave: (override: MetaOverride | null) => void;
}) {
  const initial = () => draftFrom(applyOverride(generated, override));
  const [draft, setDraft] = useState<MetaDraft>(initial);
  const [touched, setTouched] = useState(false);
  // Bản sinh ra về sau (lần kiểm tra đầu) → điền lại nếu người dùng chưa gõ gì.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!touched) setDraft(initial());
  }, [generated, override]);

  const change = (patch: Partial<MetaDraft>) => {
    setTouched(true);
    setDraft((d) => ({ ...d, ...patch }));
  };
  const tags = parseHashtags(draft.hashtags);
  const caption = fullCaption(draft);
  const problems = metaProblems(draft, limits);
  const diff = metaOverride(draft, generated);
  const savedSame = JSON.stringify(diff) === JSON.stringify(override ?? null);
  const pkgMd = (pkg?.metadata ?? {}) as GeneratedMeta;
  const source = generated?.source ? String(generated.source) : null;
  const asCheck = (): GeneratedMeta => ({ title: draft.title.trim(), description: draft.description.trim(), hashtags: tags });

  return (
    <section aria-labelledby="md-title" className="rounded-md border border-line-2 bg-bg-1">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line-1 px-3 py-2">
        <h2 id="md-title" className="text-sm font-semibold">
          Metadata · {platformName} · {langLabel(lang)}
        </h2>
        <span className="flex flex-wrap items-center gap-2">
          {override ? (
            <span className="rounded-sm bg-accent/15 px-2 text-xs text-fg-1">Đã sửa — dùng khi xuất ô này</span>
          ) : source ? (
            <span className="inline-flex items-center gap-1 rounded-sm bg-bg-3 px-2 text-xs text-fg-2">
              <Bot className="size-3" aria-hidden />
              {source === 'template' ? 'viết theo mẫu' : `viết bởi ${source}`}
            </span>
          ) : null}
        </span>
      </div>
      <div className="grid gap-3 p-3">
        {generating && !generated ? <p className="text-sm text-fg-2">Đang lấy metadata sẽ sinh ra…</p> : null}
        {limits.titleMax != null || draft.title ? (
          <Input
            id="md-title-in"
            label="Tiêu đề"
            labelExtra={<Count n={countChars(draft.title)} max={limits.titleMax} />}
            value={draft.title}
            readOnly={readOnly}
            onChange={(e) => change({ title: e.target.value })}
          />
        ) : null}
        <Textarea
          id="md-desc"
          label={limits.titleMax == null ? 'Caption' : 'Mô tả'}
          labelExtra={
            <span className="flex gap-2" title="Mô tả + hashtag — thứ nền tảng đếm">
              <Count n={countChars(caption, limits.captionUtf16)} max={limits.captionMax} />
              {limits.captionBytes != null ? <Count n={countBytes(caption)} max={limits.captionBytes} unit=" byte" /> : null}
            </span>
          }
          value={draft.description}
          readOnly={readOnly}
          minRows={3}
          maxRows={10}
          onChange={(e) => change({ description: e.target.value })}
        />
        <Input
          id="md-tags"
          label="Hashtag"
          labelExtra={<Count n={tags.length} max={limits.hashtagMax} />}
          hint="Ngăn cách bằng dấu phẩy; tự thêm # nếu thiếu."
          value={draft.hashtags}
          readOnly={readOnly}
          onChange={(e) => change({ hashtags: e.target.value })}
        />
        <label className="flex items-center gap-2 text-sm text-fg-2">
          <input type="checkbox" checked readOnly disabled className="size-4 accent-[rgb(var(--c-accent))]" />
          <Lock className="size-3.5" aria-hidden />
          Gắn nhãn nội dung do AI tạo{limits.aiLabelRequired ? ' (bắt buộc)' : ''}
        </label>
        {problems.length ? (
          <ul className="grid gap-0.5 text-xs text-state-failed" aria-live="polite">
            {problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        ) : null}
        {pkg && (pkgMd.title || pkgMd.description) ? (
          <p className="text-xs text-fg-3">
            Gói đã xuất gần nhất dùng: “{String(pkgMd.title ?? pkgMd.description ?? '').slice(0, 80)}”.
          </p>
        ) : null}
        {readOnly ? null : (
          <div className="flex flex-wrap items-center justify-end gap-2">
            {override ? (
              <Button
                variant="ghost"
                icon={RotateCcw}
                onClick={() => {
                  setTouched(false);
                  onSave(null);
                  setDraft(draftFrom(generated));
                }}
              >
                Dùng bản sinh ra
              </Button>
            ) : null}
            <Button loading={checking} onClick={() => onCheck(asCheck())}>
              Kiểm tra
            </Button>
            <Button
              variant="primary"
              disabled={problems.length > 0 || savedSame}
              title={problems.length ? 'Sửa các chỗ vượt giới hạn trước.' : undefined}
              onClick={() => {
                onSave(diff);
                setTouched(false);
              }}
            >
              {!diff && override ? 'Bỏ bản sửa' : 'Dùng khi xuất'}
            </Button>
          </div>
        )}
      </div>
    </section>
  );
}
