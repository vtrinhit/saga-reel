import { useQuery } from '@tanstack/react-query';
import { useIsViewer } from '../../api/auth';
import { qk } from '../../api/hooks';
import { callOp } from '../../api/ops';
import { ErrorState } from '../../components/ui/ErrorState';
import { ProgressBar } from '../../components/ui/ProgressBar';
import { SkeletonRows, SkeletonZone } from '../../components/ui/Skeleton';
import { formatCount } from '../../lib/format';
import { GlobalSetting, useGlobalSettings } from './GlobalSetting';
import { SectionHeader } from './SettingsLayout';

type KindStatus = { enabled: boolean; total: number | null; spent: number; remaining: number | null };

const nonNegInt = (label: string) => (v: unknown) =>
  typeof v === 'number' && Number.isInteger(v) && v >= 0 ? null : `${label} phải là số nguyên ≥ 0.`;

function SpendLine({ label, unit, s }: { label: string; unit: string; s: KindStatus | undefined }) {
  if (!s) return null;
  const limited = s.total != null;
  const pct = limited && s.total ? (s.spent / s.total) * 100 : null;
  return (
    <div className="grid gap-1.5 border-b border-line-1 px-4 py-3 last:border-b-0">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3">
        <span className="text-sm font-medium">{label}</span>
        <span className="font-mono text-sm tnum">
          {formatCount(s.spent)}
          {limited ? ` / ${formatCount(s.total!)}` : ''} {unit}
        </span>
      </div>
      {limited ? (
        <ProgressBar value={pct} tone={pct != null && pct >= 100 ? 'failed' : 'done'} label={`${label}: đã dùng`} />
      ) : null}
      <p className="text-xs text-fg-2">
        {!s.enabled
          ? 'Ngân sách đang tắt — không giới hạn.'
          : limited
            ? `Còn ${formatCount(s.remaining ?? 0)} ${unit}.`
            : 'Không giới hạn (trần = 0).'}
      </p>
    </div>
  );
}

/** Settings › Ngân sách & song song: trần ảnh/video (0 = không giới hạn), đã dùng, số job ffmpeg song song. */
export function SettingsBudget() {
  const viewer = useIsViewer();
  const { explain, schema } = useGlobalSettings();
  const budget = useQuery({
    queryKey: qk.budget,
    queryFn: async () => (await callOp('budget.status', {})) as { image: KindStatus; video: KindStatus },
  });

  // Người xem: chỉ xem (control khoá, không có "về mặc định").
  const common = { resolved: explain.data, schema: schema.data, disabled: viewer };
  const loading = !explain.data || !schema.data;

  return (
    <div className="grid gap-8">
      <section className="grid gap-4">
        <SectionHeader
          title="Ngân sách"
          description="Trần số ảnh/video được tạo. Kết nối giả (fake) không tính. Vượt trần thì việc tạo bị chặn trước khi gọi provider."
        />
        {budget.isError ? (
          <ErrorState what="Không tải được số đã dùng" error={budget.error} onRetry={() => void budget.refetch()} />
        ) : budget.data ? (
          <div className="overflow-hidden rounded-md border border-line-2 bg-bg-1">
            <SpendLine label="Ảnh" unit="ảnh" s={budget.data.image} />
            <SpendLine label="Video" unit="video" s={budget.data.video} />
          </div>
        ) : (
          <SkeletonZone label="Đang tải ngân sách">
            <SkeletonRows count={2} />
          </SkeletonZone>
        )}
        {explain.isError ? (
          <ErrorState what="Không tải được cài đặt" error={explain.error} onRetry={() => void explain.refetch()} />
        ) : loading ? null : (
          <div className="grid max-w-xl gap-4">
            <GlobalSetting {...common} settingKey="budget.enabled" label="Bật giới hạn ngân sách" />
            <GlobalSetting
              {...common}
              settingKey="budget.images_total"
              label="Trần số ảnh"
              suffix="0 = không giới hạn."
              validate={nonNegInt('Trần số ảnh')}
            />
            <GlobalSetting
              {...common}
              settingKey="budget.videos_total"
              label="Trần số video"
              suffix="0 = không giới hạn (vd video GrokTele không tính tiền)."
              validate={nonNegInt('Trần số video')}
            />
          </div>
        )}
      </section>

      <section className="grid gap-4">
        <SectionHeader title="Chạy song song" description="Số việc chạy cùng lúc. Số việc trên từng kết nối provider đặt trong Sửa kết nối." />
        {loading ? null : (
          <div className="grid max-w-xl gap-4">
            <GlobalSetting
              {...common}
              settingKey="jobs.ffmpeg_concurrency"
              label="Số việc ffmpeg chạy song song"
              hint="Dựng video, ghép âm thanh, xuất gói."
              validate={(v) => (typeof v === 'number' && Number.isInteger(v) && v >= 1 && v <= 16 ? null : 'Từ 1 đến 16.')}
            />
          </div>
        )}
      </section>
    </div>
  );
}
