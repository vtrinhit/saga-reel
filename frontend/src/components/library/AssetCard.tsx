import { AudioLines, Lock, Sparkles } from 'lucide-react';
import type { MouseEvent, ReactNode } from 'react';
import { Link } from 'react-router';
import { cn, focusRing } from '../../lib/cn';
import { assetCover, lookChips, type Asset } from '../../lib/library';
import { StatusPill } from '../ui/StatusPill';
import { MediaThumb } from './MediaThumb';

const CHIP_MAX = 2;

/**
 * AssetCard (DESIGN §5.2): ảnh 1:1 (mỏ neo của look mặc định), mã ở góc trái, khoá ở góc phải; tên `sm` 600; chip look
 * (tối đa 2 + "+n"); dòng giọng + số look. Đang mở = viền accent bên trong (không shadow).
 */
export function AssetCard({
  asset,
  href,
  selected,
  voiceName,
  onOpen,
}: {
  asset: Asset;
  href: string;
  selected?: boolean;
  voiceName?: string | null;
  /** Bấm thường (không Ctrl/⌘): mở trong inspector thay vì đổi trang. Trả false để đi theo link. */
  onOpen?: () => boolean;
}) {
  const chips = lookChips(asset);
  const looks = asset.looks.length;
  const onClick = (e: MouseEvent<HTMLAnchorElement>) => {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
    if (onOpen?.()) e.preventDefault();
  };
  return (
    <Link
      to={href}
      onClick={onClick}
      aria-current={selected ? 'true' : undefined}
      className={cn(
        'group relative grid min-w-0 overflow-hidden rounded-md border border-line-1 bg-bg-2 text-left',
        'transition-colors duration-fast ease-out hover:border-line-3',
        focusRing,
        selected &&
          'after:pointer-events-none after:absolute after:inset-0 after:rounded-md after:border-2 after:border-accent after:content-[""]',
      )}
    >
      <div className="relative">
        <MediaThumb mediaId={assetCover(asset)} alt="" width={360} className="aspect-square w-full" />
        <span className="sr-pill-on-media absolute left-2 top-2 rounded-xs px-1.5 font-mono text-2xs font-semibold text-fg-1">
          {asset.key}
        </span>
        {asset.locked ? (
          <span className="sr-pill-on-media absolute right-2 top-2 grid h-6 w-6 place-items-center rounded-xs text-fg-1">
            <Lock className="h-3.5 w-3.5" strokeWidth={1.75} aria-label="Đã khoá mô tả" />
          </span>
        ) : null}
      </div>
      <div className="grid min-w-0 gap-1.5 px-2.5 pb-2.5 pt-2">
        <span className="truncate text-sm font-semibold">{asset.name}</span>
        {chips.length ? (
          <span className="flex min-w-0 flex-wrap gap-1">
            {chips.slice(0, CHIP_MAX).map((c) => (
              <span key={c} className="max-w-full truncate rounded-xs bg-bg-3 px-1.5 text-xs text-fg-1">
                {c}
              </span>
            ))}
            {chips.length > CHIP_MAX ? (
              <span className="rounded-xs bg-bg-3 px-1.5 text-xs text-fg-2">+{chips.length - CHIP_MAX}</span>
            ) : null}
          </span>
        ) : null}
        <span className="flex min-w-0 items-center gap-1 text-xs text-fg-2">
          {asset.kind === 'character' ? (
            <>
              <AudioLines className="h-3 w-3 flex-none text-fg-3" strokeWidth={1.75} aria-hidden />
              <span className="truncate">{voiceName ?? 'chưa gán giọng'}</span>
              <span aria-hidden>·</span>
            </>
          ) : null}
          <span className="whitespace-nowrap">{looks} look</span>
        </span>
      </div>
    </Link>
  );
}

/** Thẻ tài sản bible gợi ý: chưa có ảnh, pill `wait` "chờ xác nhận" + nút tạo. */
export function SuggestedAssetCard({
  name,
  code,
  note,
  action,
}: {
  name: string;
  code: string;
  note: string;
  action: ReactNode;
}) {
  return (
    <div className="grid min-w-0 overflow-hidden rounded-md border border-dashed border-line-2 bg-bg-1">
      <div className="relative grid aspect-square place-items-center bg-bg-1 text-fg-3">
        <Sparkles className="h-8 w-8" strokeWidth={1.5} aria-hidden />
        <span className="absolute left-2 top-2 rounded-xs bg-bg-3 px-1.5 font-mono text-2xs font-semibold text-fg-2">
          {code}
        </span>
      </div>
      <div className="grid min-w-0 gap-1.5 px-2.5 pb-2.5 pt-2">
        <span className="truncate text-sm font-semibold">{name}</span>
        <StatusPill status="wait" label="chờ xác nhận" className="justify-self-start" />
        <span className="line-clamp-2 text-xs text-fg-2">{note}</span>
        {action}
      </div>
    </div>
  );
}
