/**
 * Player của Studio (DESIGN §5.4): nền stage, khung giữ tỉ lệ tập, bản dựng mới nhất qua `/media/<id>` (server hỗ trợ
 * Range nên tua được), lớp safe-zone theo preset nền tảng (phím `G`), transport ◂ ▶ ▸ + timecode `HH:MM:SS:FF`.
 * Thời gian là của trang (playhead dùng chung với timeline): video báo `timeupdate`/rAF, trang tua bằng `seekTo`.
 */
import { Film, Pause, Play, SkipBack, SkipForward, Volume2, VolumeX } from 'lucide-react';
import { forwardRef, useEffect, useImperativeHandle, useRef, useState, type ReactNode } from 'react';
import { cn } from '../../lib/cn';
import { safeInsets } from '../../lib/studioSafeZone';
import type { PlatformData } from '../../lib/studioData';
import { timecode } from '../../lib/timeline';
import { IconButton } from '../ui/IconButton';
import './studio.css';

export interface PlayerHandle {
  play: () => void;
  pause: () => void;
  toggle: () => void;
  seekTo: (ms: number) => void;
  setRate: (r: number) => void;
  playing: () => boolean;
}

export interface PlayerProps {
  src: string | null;
  /** Tỉ lệ khung (w/h). */
  ratio: number;
  timeMs: number;
  totalMs: number;
  onTime: (ms: number) => void;
  onPlayingChange?: (p: boolean) => void;
  safeZone: { label: string; data: PlatformData } | null;
  /** Phụ đề xem trước (khi chưa có bản dựng). */
  subtitle?: string | null;
  /** Nội dung khi chưa có bản dựng (nút Xem trước…). */
  empty?: ReactNode;
  /** Select nền tảng + nút lớp, đặt bên phải transport. */
  tools?: ReactNode;
  className?: string;
  fps?: number;
}

export const Player = forwardRef<PlayerHandle, PlayerProps>(function Player(
  { src, ratio, timeMs, totalMs, onTime, onPlayingChange, safeZone, subtitle, empty, tools, className, fps = 30 },
  ref,
) {
  const video = useRef<HTMLVideoElement>(null);
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(false);
  const [failed, setFailed] = useState(false);
  const [rate, setRateState] = useState(1);
  const raf = useRef(0);

  useImperativeHandle(ref, () => ({
    play: () => void video.current?.play().catch(() => undefined),
    pause: () => video.current?.pause(),
    toggle: () => {
      const v = video.current;
      if (!v) return;
      if (v.paused) void v.play().catch(() => undefined);
      else v.pause();
    },
    seekTo: (ms: number) => {
      const v = video.current;
      if (v && Number.isFinite(ms)) v.currentTime = Math.max(0, ms / 1000);
      else onTime(Math.max(0, Math.min(totalMs, ms)));
    },
    setRate: (r: number) => {
      if (video.current) video.current.playbackRate = r;
      setRateState(r);
    },
    playing: () => !!video.current && !video.current.paused,
  }));

  useEffect(() => {
    if (!playing) return;
    const tick = () => {
      if (video.current) onTime(video.current.currentTime * 1000);
      raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf.current);
  }, [playing, onTime]);

  useEffect(() => {
    onPlayingChange?.(playing);
  }, [playing, onPlayingChange]);

  const ins = safeZone ? safeInsets(safeZone.data) : null;
  const durationMs = (video.current?.duration ?? 0) * 1000 || totalMs;

  return (
    <div className={cn('flex min-h-0 min-w-0 flex-col', className)}>
      <div className="relative flex min-h-0 flex-1 items-center justify-center overflow-hidden bg-stage">
        <div
          className="relative max-h-full max-w-full"
          style={{ aspectRatio: String(ratio), height: ratio < 1 ? '100%' : undefined, width: ratio >= 1 ? '100%' : undefined }}
        >
          {src ? (
            <video
              ref={video}
              key={src}
              src={src}
              className="absolute inset-0 size-full bg-stage object-contain"
              preload="auto"
              playsInline
              // Giữ vị trí playhead khi đổi bản dựng; tua nhẹ khỏi 0 để trình duyệt giải mã khung đầu (không đen).
              onLoadedMetadata={(e) => {
                setFailed(false);
                e.currentTarget.currentTime = Math.max(0.04, timeMs / 1000);
              }}
              onError={() => setFailed(true)}
              muted={muted}
              onPlay={() => setPlaying(true)}
              onPause={() => setPlaying(false)}
              onEnded={() => setPlaying(false)}
              onTimeUpdate={(e) => !playing && onTime(e.currentTarget.currentTime * 1000)}
              onSeeked={(e) => onTime(e.currentTarget.currentTime * 1000)}
              aria-label="Bản dựng của tập"
            />
          ) : (
            <div className="absolute inset-0 flex flex-col items-center justify-start gap-3 bg-bg-0 p-4 pt-[22%] text-center">
              <Film className="size-8 text-fg-3" aria-hidden />
              {empty}
            </div>
          )}
          {src && failed ? (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-bg-0 p-4 text-center" role="alert">
              <Film className="size-8 text-fg-3" aria-hidden />
              <p className="max-w-xs text-sm text-fg-2">Trình duyệt này không phát được bản dựng (MP4 H.264). Tải file về để xem.</p>
              <a href={src} download className="text-sm underline underline-offset-2">
                Tải bản dựng
              </a>
            </div>
          ) : null}
          {!src && subtitle ? (
            <p className="sr-sub-preview pointer-events-none absolute inset-x-[10%] bottom-[10%] text-balance text-center text-md font-semibold">
              {subtitle}
            </p>
          ) : null}
          {ins ? (
            <div className="pointer-events-none absolute inset-0" aria-hidden>
              <div className="sr-safe-dim absolute inset-x-0 top-0" style={{ height: `${ins.top}%` }} />
              <div className="sr-safe-dim absolute inset-x-0 bottom-0" style={{ height: `${ins.bottom}%` }} />
              <div className="sr-safe-dim absolute left-0" style={{ top: `${ins.top}%`, bottom: `${ins.bottom}%`, width: `${ins.left}%` }} />
              <div className="sr-safe-dim absolute right-0" style={{ top: `${ins.top}%`, bottom: `${ins.bottom}%`, width: `${ins.right}%` }} />
              <div
                className="sr-safe-box absolute"
                style={{ top: `${ins.top}%`, bottom: `${ins.bottom}%`, left: `${ins.left}%`, right: `${ins.right}%` }}
              />
              <span className="sr-safe-label absolute left-1.5 top-1.5 rounded-xs px-1.5 text-2xs font-medium">
                {safeZone!.label} · {ins.titleSafe ? 'vùng tiêu đề 90%' : 'vùng an toàn'}
              </span>
            </div>
          ) : null}
        </div>
      </div>
      <div className="flex min-w-0 flex-wrap items-center gap-1 border-t border-line-1 bg-bg-1 px-2 py-1.5">
        <IconButton icon={SkipBack} label="Lùi 1 khung (←)" onClick={() => seekBy(-1000 / fps)} disabled={!src} />
        <IconButton
          icon={playing ? Pause : Play}
          label={playing ? 'Dừng (Space)' : 'Phát (Space)'}
          onClick={() => {
            const v = video.current;
            if (!v) return;
            if (v.paused) void v.play().catch(() => undefined);
            else v.pause();
          }}
          disabled={!src}
        />
        <IconButton icon={SkipForward} label="Tiến 1 khung (→)" onClick={() => seekBy(1000 / fps)} disabled={!src} />
        <p className="ml-1 whitespace-nowrap font-mono text-sm tabular-nums" aria-live="off">
          <span className="text-fg-1">{timecode(timeMs, fps)}</span>
          <span className="text-fg-3"> / {timecode(durationMs, fps)}</span>
        </p>
        <label className="sr-only" htmlFor="sr-player-rate">
          Tốc độ phát
        </label>
        <select
          id="sr-player-rate"
          className="ml-1 hidden h-control rounded-sm border border-line-3 bg-bg-2 px-1 font-mono text-xs sm:block"
          value={rate}
          onChange={(e) => {
            const r = Number(e.target.value);
            if (video.current) video.current.playbackRate = r;
            setRateState(r);
          }}
          disabled={!src}
        >
          {[0.5, 1, 1.5, 2].map((r) => (
            <option key={r} value={r}>
              {String(r).replace('.', ',')}×
            </option>
          ))}
        </select>
        <IconButton icon={muted ? VolumeX : Volume2} label={muted ? 'Bật tiếng' : 'Tắt tiếng'} pressed={muted} onClick={() => setMuted((m) => !m)} disabled={!src} />
        <div className="ml-auto flex min-w-0 items-center gap-1">{tools}</div>
      </div>
    </div>
  );

  function seekBy(ms: number) {
    const v = video.current;
    if (!v) return;
    v.pause();
    v.currentTime = Math.max(0, v.currentTime + ms / 1000);
  }
});
