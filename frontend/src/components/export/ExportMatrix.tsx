/**
 * Ma trận xuất (brief §3.7): hàng = nền tảng, cột = ngôn ngữ, ô ≥ 200px; nằm trong `role=region` cuộn ngang khi hẹp
 * (`position: relative` để `sr-only` không thoát khung). Mỗi ô tự chạy `export.check` (khoá theo tập·nền tảng·ngôn
 * ngữ, giữ 5 phút — kiểm tra có bản cuối thì đo loudness bằng ffmpeg nên không chạy lại liên tục).
 */
import { useQuery } from '@tanstack/react-query';
import { callOp } from '../../api/ops';
import { cellKey, durationLimit, langLabel, type CellKey, type GeneratedMeta } from '../../lib/exportMatrix';
import { errorText, sk, type CheckReport, type PlatformData, type PresetRow } from '../../lib/studioData';
import { cn, focusRing } from '../../lib/cn';
import { ExportCell } from './ExportCell';

export function useCheck(seqId: number | null, platform: string, lang: string, metadata?: GeneratedMeta | null) {
  return useQuery({
    queryKey: metadata ? [...sk.check(seqId ?? -1, platform, lang), 'md', metadata] : sk.check(seqId ?? -1, platform, lang),
    queryFn: async () =>
      (await callOp('export.check', { sequence_id: seqId!, platform, lang, split_parts: true, metadata: metadata ?? null })) as unknown as CheckReport,
    enabled: seqId != null && !!platform && !!lang,
    staleTime: 5 * 60_000,
    retry: false,
  });
}

function Cell(props: {
  seqId: number;
  platform: string;
  lang: string;
  checked: boolean;
  active: boolean;
  missing: number;
  translateHref?: string;
  readOnly?: boolean;
  onToggle: (v: boolean) => void;
  onActivate: () => void;
}) {
  const q = useCheck(props.seqId, props.platform, props.lang);
  return (
    <ExportCell
      id={`cell-${props.platform}-${props.lang}`}
      report={q.data ?? null}
      loading={q.isPending}
      error={q.isError ? errorText('Không kiểm được', q.error) : null}
      checked={props.checked}
      active={props.active}
      missingTranslations={props.missing}
      translateHref={props.translateHref}
      readOnly={props.readOnly}
      onToggle={props.onToggle}
      onActivate={props.onActivate}
    />
  );
}

export function ExportMatrix({ seqId, audioHref, platforms, langs, selected, active, missingByLang, readOnly, onToggle, onActivate }: {
  seqId: number;
  audioHref?: string;
  platforms: PresetRow<PlatformData>[];
  langs: string[];
  selected: Set<CellKey>;
  active: CellKey | null;
  missingByLang: Record<string, number>;
  readOnly?: boolean;
  onToggle: (k: CellKey, v: boolean) => void;
  onActivate: (k: CellKey) => void;
}) {
  const cols = `minmax(9rem, 12rem) repeat(${langs.length}, minmax(200px, 1fr))`;
  return (
    <div role="region" aria-label="Ma trận nền tảng × ngôn ngữ" tabIndex={0} className={cn('relative overflow-x-auto', focusRing)}>
      <div className="grid min-w-0 gap-3" style={{ gridTemplateColumns: cols }}>
        <div className="text-sm font-semibold text-fg-2">Nền tảng</div>
        {langs.map((l) => (
          <div key={l} className="text-sm font-semibold text-fg-2">
            {langLabel(l)}
          </div>
        ))}
        {platforms.map((p) => (
          <div key={p.key} className="contents">
            <div className="self-center">
              <p className="text-sm font-semibold">{p.name}</p>
              <p className="text-xs text-fg-2">
                <span className="font-mono tabular-nums">
                  {p.data.width && p.data.height ? `${p.data.width}×${p.data.height}` : p.data.aspect}
                </span>{' '}
                · {durationLimit(p.data)}
              </p>
            </div>
            {langs.map((l) => {
              const k = cellKey(p.key, l);
              return (
                <Cell
                  key={k}
                  seqId={seqId}
                  platform={p.key}
                  lang={l}
                  checked={selected.has(k)}
                  active={active === k}
                  missing={missingByLang[l] ?? 0}
                  translateHref={audioHref ? `${audioHref}?lang=${l}` : undefined}
                  readOnly={readOnly}
                  onToggle={(v) => onToggle(k, v)}
                  onActivate={() => onActivate(k)}
                />
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}
