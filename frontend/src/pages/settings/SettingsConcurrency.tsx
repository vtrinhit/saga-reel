import { useIsViewer } from '../../api/auth';
import { ErrorState } from '../../components/ui/ErrorState';
import { GlobalSetting, useGlobalSettings } from './GlobalSetting';
import { SectionHeader } from './SettingsLayout';

/** Settings › Chạy song song: số job ffmpeg chạy cùng lúc (số việc trên từng kết nối đặt trong Sửa kết nối). */
export function SettingsConcurrency() {
  const viewer = useIsViewer();
  const { explain, schema } = useGlobalSettings();
  // Người xem: chỉ xem (control khoá, không có "về mặc định").
  const common = { resolved: explain.data, schema: schema.data, disabled: viewer };
  const loading = !explain.data || !schema.data;

  return (
    <div className="grid gap-8">
      <section className="grid gap-4">
        <SectionHeader title="Chạy song song" description="Số việc chạy cùng lúc. Số việc trên từng kết nối provider đặt trong Sửa kết nối." />
        {explain.isError ? (
          <ErrorState what="Không tải được cài đặt" error={explain.error} onRetry={() => void explain.refetch()} />
        ) : loading ? null : (
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
