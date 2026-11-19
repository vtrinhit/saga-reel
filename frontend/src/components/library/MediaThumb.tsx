import { ImageOff } from 'lucide-react';
import { useState } from 'react';
import { cn } from '../../lib/cn';
import { thumbUrl } from '../../lib/library';

/**
 * Ảnh thu nhỏ trên nền `stage`: tải lười, hiện dần bằng opacity (không filter/blur — DESIGN §5.2), lỗi tải → icon.
 * Kích thước do khung cha quyết định (`aspect-*`), ảnh `object-cover`.
 */
export function MediaThumb({
  mediaId,
  width = 320,
  alt,
  className,
  fit = 'cover',
}: {
  mediaId: number | null | undefined;
  width?: number;
  alt: string;
  className?: string;
  fit?: 'cover' | 'contain';
}) {
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);
  return (
    <div className={cn('relative overflow-hidden bg-stage', className)}>
      {mediaId != null && !failed ? (
        <img
          src={thumbUrl(mediaId, width)}
          alt={alt}
          loading="lazy"
          decoding="async"
          onLoad={() => setLoaded(true)}
          onError={() => setFailed(true)}
          className={cn(
            'absolute inset-0 h-full w-full transition-opacity duration-image ease-out',
            fit === 'cover' ? 'object-cover' : 'object-contain',
            loaded ? 'opacity-100' : 'opacity-0',
          )}
        />
      ) : null}
      {mediaId != null && failed ? (
        <div className="absolute inset-0 grid place-items-center text-fg-3">
          <ImageOff className="sr-ico" aria-label="Không tải được ảnh" />
        </div>
      ) : null}
    </div>
  );
}
