/**
 * Âm thanh của tập `/p/:pid/s/:seqId/audio` (brief §3.6, prototype `audio.html`): thoại nhóm theo clip với trạng thái
 * TTS từng câu, SegmentedControl ngôn ngữ; inspector: giọng theo nhân vật, kiểu phụ đề, kho nhạc/SFX có giấy phép.
 * "Kế hoạch thời lượng" → bảng đề xuất → Áp dụng. "Đọc n câu còn lại" → `audio.generate` (TTS không tính lượt).
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AudioLines, Clock, MessageSquareText } from 'lucide-react';
import { useMemo, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router';
import { useIsViewer } from '../../api/auth';
import { callOpLoose } from '../../api/opsLoose';
import type { Job } from '../../api/types';
import { DialogueLine } from '../../components/audio/DialogueLine';
import { DurationPlanDialog, MODE_LABEL } from '../../components/audio/DurationPlanDialog';
import { SoundLibrary } from '../../components/audio/SoundLibrary';
import { SubtitleStyleSelect } from '../../components/audio/SubtitleStyleSelect';
import { useAudioPlayback } from '../../components/audio/useAudioPlayback';
import { VoiceRow, voiceSummary } from '../../components/audio/VoiceRow';
import { InspectorSection } from '../../components/studio/StudioInspector';
import { useStudioMutations } from '../../components/studio/useStudioMutations';
import '../../components/studio/studio.css';
import { Button } from '../../components/ui/Button';
import { EmptyState } from '../../components/ui/EmptyState';
import { ErrorState } from '../../components/ui/ErrorState';
import { SegmentedControl } from '../../components/ui/SegmentedControl';
import { SkeletonRows, SkeletonZone } from '../../components/ui/Skeleton';
import { langLabel } from '../../lib/exportMatrix';
import {
  numId,
  sk,
  toastError,
  useAssets,
  useClips,
  useLines,
  usePresets,
  useProjectRow,
  useSequenceRow,
  useSounds,
  useStudioLiveRefresh,
  useTimelineItems,
  useVoices,
  type VoiceProfile,
} from '../../lib/studioData';
import { toast } from '../../stores/toast';

const NARRATOR = -1;

export function AudioPage() {
  const { projectId, seqId } = useParams();
  const pid = numId(projectId);
  const sid = numId(seqId);
  const base = `/p/${projectId}`;
  const viewer = useIsViewer();
  const qc = useQueryClient();
  useStudioLiveRefresh();

  const project = useProjectRow(pid);
  const sequence = useSequenceRow(sid);
  const clipsQ = useClips(sid);
  const linesQ = useLines(sid);
  const itemsQ = useTimelineItems(sid);
  const voicesQ = useVoices(pid);
  const assetsQ = useAssets(pid);
  const soundsQ = useSounds(pid);
  const styleQ = usePresets<{ max_lines?: number; max_chars_per_second?: number }>('subtitle_style', pid);
  const m = useStudioMutations(pid, sid);
  const play = useAudioPlayback();

  const primaryLang = project.data?.language ?? 'vi';
  const langs = useMemo(() => {
    const ls = [primaryLang, ...((project.data?.languages as string[] | undefined) ?? [])];
    return Array.from(new Set(ls.filter(Boolean)));
  }, [primaryLang, project.data?.languages]);
  const [search] = useSearchParams();
  const [langSel, setLang] = useState<string | null>(() => search.get('lang'));
  const lang = langSel && langs.includes(langSel) ? langSel : primaryLang;
  const primary = lang === primaryLang;

  const clips = useMemo(() => clipsQ.data ?? [], [clipsQ.data]);
  const lines = useMemo(() => linesQ.data ?? [], [linesQ.data]);
  const voices = voicesQ.data ?? [];
  const assets = assetsQ.data ?? [];
  const voiceById = new Map(voices.map((v) => [v.id, v]));
  const assetById = new Map(assets.map((a) => [a.id, a]));
  const narratorProfile = voiceById.get(Number(project.data?.settings?.narrator_voice_profile_id)) ?? null;

  // Trạng thái TTS theo câu: việc `audio.tts` mới nhất của mỗi dialogue_id.
  const ttsJobs = useQuery({
    queryKey: sk.ttsJobs(pid ?? -1),
    queryFn: async () => (await callOpLoose<{ items: Job[] }>('job.list', { project_id: pid, type: 'audio.tts', limit: 300 })).items,
    enabled: pid != null,
    refetchInterval: (q) => ((q.state.data ?? []).some((j) => ['queued', 'running', 'pending'].includes(j.status)) ? 4000 : false),
  });
  const jobByLine = useMemo(() => {
    const map = new Map<number, Job>();
    for (const j of ttsJobs.data ?? []) {
      const did = Number(j.params?.dialogue_id);
      const jl = String(j.params?.lang ?? primaryLang);
      if (!did || jl !== lang) continue;
      if (!map.has(did)) map.set(did, j); // job.list mới nhất trước
    }
    return map;
  }, [ttsJobs.data, lang, primaryLang]);

  const ttsLines = lines.filter((l) => l.source !== 'grok' && l.source !== 'none');
  const readCount = primary ? ttsLines.filter((l) => l.audio_take_id).length : 0;

  // Số câu cần đọc (dry_run, không tạo việc).
  const plan = useQuery({
    queryKey: ['studio', 'tts-plan', sid, lang, lines.length],
    queryFn: () => callOpLoose<{ estimate: { lines: number; cached: number } }>('audio.generate', { sequence_id: sid, lang, dry_run: true }),
    enabled: sid != null && !viewer && lines.length > 0,
  });
  const todo = plan.data?.estimate.lines ?? null;

  const generate = useMutation({
    mutationFn: () => callOpLoose<{ job_ids: number[]; cached: unknown[] }>('audio.generate', { sequence_id: sid, lang }),
    onSuccess: (r) => {
      toast({ tone: 'success', message: r.job_ids.length ? `Đã xếp ${r.job_ids.length} câu để đọc.` : 'Mọi câu đã có âm thanh (dùng lại bản đã đọc).' });
      void qc.invalidateQueries({ queryKey: sk.ttsJobs(pid ?? -1) });
      void qc.invalidateQueries({ queryKey: sk.lines(sid ?? -1) });
      void plan.refetch();
    },
    onError: (e) => toastError('Không bắt đầu đọc thoại được', e),
  });

  const assignVoice = useMutation({
    mutationFn: async ({ speaker, profileId }: { speaker: number; profileId: number | null }) => {
      if (speaker === NARRATOR) {
        const settings = { ...(project.data?.settings ?? {}), narrator_voice_profile_id: profileId };
        return callOpLoose('project.update', { id: pid, values: { settings }, expected_updated_at: project.data?.updated_at ?? null });
      }
      const a = assetById.get(speaker);
      return callOpLoose('asset.update', { id: speaker, values: { voice_profile_id: profileId }, expected_updated_at: a?.updated_at ?? null });
    },
    onSuccess: () => {
      toast({ tone: 'success', message: 'Đã đổi giọng. Câu đã đọc sẽ được đọc lại khi bạn bấm Đọc.' });
      void qc.invalidateQueries({ queryKey: sk.assets(pid ?? -1) });
      void qc.invalidateQueries({ queryKey: sk.project(pid ?? -1) });
      void plan.refetch();
    },
    onError: (e) => toastError('Không đổi được giọng', e),
  });

  const seqSettings = (sequence.data?.settings ?? {}) as Record<string, unknown>;
  const styleKey = String(seqSettings.subtitle_style ?? project.data?.settings?.subtitle_style ?? ((project.data?.aspect ?? '9:16').startsWith('16') ? '16x9' : '9x16'));
  const setStyle = useMutation({
    mutationFn: (key: string) =>
      callOpLoose('sequence.update', {
        id: sid,
        values: { settings: { ...seqSettings, subtitle_style: key } },
        expected_updated_at: sequence.data?.updated_at ?? null,
      }),
    onSuccess: (_r, key) => {
      toast({ tone: 'success', message: `Tập dùng kiểu phụ đề ${key}.` });
      void qc.invalidateQueries({ queryKey: sk.sequence(sid ?? -1) });
    },
    onError: (e) => toastError('Không đổi được kiểu phụ đề', e),
  });

  const [planOpen, setPlanOpen] = useState(false);

  // Người nói trong tập: nhân vật có câu + người kể.
  const speakers = useMemo(() => {
    const ids = new Set<number>();
    for (const l of lines) ids.add(l.speaker_asset_id ?? NARRATOR);
    return [...ids].sort((a, b) => (a === NARRATOR ? 1 : b === NARRATOR ? -1 : a - b));
  }, [lines]);
  const profileFor = (speaker: number): VoiceProfile | null =>
    speaker === NARRATOR ? narratorProfile : (voiceById.get(Number(assetById.get(speaker)?.voice_profile_id)) ?? null);
  const speakerName = (speaker: number) => (speaker === NARRATOR ? 'Người kể' : (assetById.get(speaker)?.name ?? `Nhân vật #${speaker}`));

  // Nhóm câu theo clip (thứ tự clip), tiêu đề "Cảnh n · Clip".
  const groups = useMemo(() => {
    const sceneNo = new Map<number | null, number>();
    const out: { clipKey: string; scene: number | null; clipId: number; items: typeof lines }[] = [];
    for (const c of clips) {
      if (!sceneNo.has(c.scene_id)) sceneNo.set(c.scene_id, sceneNo.size + 1);
      const ls = lines.filter((l) => l.clip_id === c.id);
      if (ls.length) out.push({ clipKey: c.key, scene: c.scene_id != null ? sceneNo.get(c.scene_id)! : null, clipId: c.id, items: ls });
    }
    return out;
  }, [clips, lines]);
  const usedSoundIds = new Set((itemsQ.data ?? []).map((i) => i.sound_id).filter((x): x is number => x != null));
  const sample = lines.find((l) => l.subtitle && l.text.trim())?.text ?? null;
  const syncMode = String(project.data?.settings?.['audio.duration_sync'] ?? 'voice_drives') as keyof typeof MODE_LABEL;

  if (sid == null || pid == null) return <EmptyState icon={AudioLines} title="Đường dẫn không đúng." description="Mở Âm thanh từ thanh chặng của một tập." />;
  if (sequence.isError || linesQ.isError) {
    return <ErrorState what="Không tải được thoại của tập" error={sequence.error ?? linesQ.error} onRetry={() => void linesQ.refetch()} />;
  }
  const loading = linesQ.isPending || clipsQ.isPending;

  return (
    <div className="grid min-w-0 gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="grid min-w-0 gap-1">
          <h1 className="text-balance text-xl">Âm thanh</h1>
          <p className="text-sm text-fg-2">
            {sequence.data ? `Tập ${sequence.data.number}` : 'Tập'} · {lines.length} câu thoại
            {primary ? ` · ${readCount} đã đọc` : ''} · khớp thời lượng: {MODE_LABEL[syncMode] ?? syncMode}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button icon={Clock} onClick={() => setPlanOpen(true)} disabled={!lines.length}>
            Kế hoạch thời lượng
          </Button>
          {!viewer ? (
            <Button
              variant="primary"
              icon={AudioLines}
              loading={generate.isPending}
              disabled={!lines.length || todo === 0}
              onClick={() => generate.mutate()}
              cost={<span className="font-mono text-2xs opacity-80">miễn phí</span>}
            >
              {todo == null ? 'Đọc thoại' : todo === 0 ? 'Đã đọc hết' : `Đọc ${todo} câu còn lại`}
            </Button>
          ) : null}
        </div>
      </div>

      <div className="grid min-w-0 gap-4 xl:grid-cols-[minmax(0,1fr)_var(--w-inspector)] xl:items-start">
        <section aria-labelledby="dl-title" className="min-w-0 rounded-md border border-line-2 bg-bg-1">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line-1 px-3 py-2">
            <h2 id="dl-title" className="text-sm font-semibold">
              Thoại theo clip
            </h2>
            {langs.length > 1 ? (
              <SegmentedControl label="Ngôn ngữ thoại" value={lang} options={langs.map((l) => ({ value: l, label: langLabel(l) }))} onChange={setLang} />
            ) : null}
          </div>
          {loading ? (
            <SkeletonZone label="Đang tải thoại">
              <SkeletonRows count={6} />
            </SkeletonZone>
          ) : groups.length === 0 ? (
            <EmptyState
              icon={MessageSquareText}
              title="Tập này chưa có thoại."
              description="Thêm lời thoại cho clip ở bảng clip, hoặc để AI viết thoại từ kịch bản."
              action={
                <Link className="text-sm underline" to={`${base}/s/${sid}`}>
                  Mở bảng clip
                </Link>
              }
            />
          ) : (
            groups.map((g) => (
              <div key={g.clipId}>
                <h3 className="sticky top-[var(--sr-top,0px)] z-[1] border-b border-line-1 bg-bg-2 px-3 py-1.5 text-sm font-semibold">
                  {g.scene != null ? `Cảnh ${g.scene} · ` : ''}
                  {g.clipKey}
                </h3>
                <ul>
                  {g.items.map((l) => {
                    const sp = l.speaker_asset_id ?? NARRATOR;
                    const prof = profileFor(sp);
                    return (
                      <DialogueLine
                        key={l.id}
                        line={l}
                        speaker={speakerName(sp)}
                        voiceLabel={l.source === 'grok' ? 'âm gốc Grok' : prof ? voiceSummary(prof, [lang]).replace(/^[a-z]+: /, '') : 'giọng mặc định'}
                        lang={lang}
                        primary={primary}
                        job={jobByLine.get(l.id) ?? null}
                        playing={play.playing === `line-${l.id}`}
                        loadingAudio={play.loading === `line-${l.id}`}
                        readOnly={viewer}
                        onPlay={() => void play.playLine(l.id, l.audio_take_id)}
                        onSave={(v) => void m.updateLine(l, v, primary ? 'lời thoại' : `bản dịch ${lang}`)}
                      />
                    );
                  })}
                </ul>
              </div>
            ))
          )}
        </section>

        <aside aria-label="Giọng, phụ đề, nhạc" className="min-w-0 overflow-hidden rounded-md border border-line-2 bg-bg-1 xl:sticky xl:top-[calc(var(--sr-top,0px)+16px)] xl:max-h-[calc(100dvh-var(--sr-top,0px)-32px)] xl:overflow-y-auto">
          <InspectorSection title="Giọng theo nhân vật">
            {speakers.length ? (
              <ul className="-mx-3 -mb-3">
                {speakers.map((sp) => (
                  <VoiceRow
                    key={sp}
                    name={speakerName(sp)}
                    profile={profileFor(sp)}
                    voices={voices}
                    langs={langs}
                    readOnly={viewer}
                    playing={play.playing === `voice-${sp}`}
                    loading={play.loading === `voice-${sp}`}
                    onAssign={(profileId) => assignVoice.mutate({ speaker: sp, profileId })}
                    onPreview={() =>
                      void play.previewVoice(`voice-${sp}`, {
                        text: lines.find((l) => (l.speaker_asset_id ?? NARRATOR) === sp)?.text || 'Xin chào, đây là giọng thử.',
                        voice_profile_id: profileFor(sp)?.id ?? null,
                        lang,
                      })
                    }
                  />
                ))}
              </ul>
            ) : (
              <p className="text-sm text-fg-2">Chưa có người nói trong tập.</p>
            )}
          </InspectorSection>
          <InspectorSection title="Phụ đề">
            <SubtitleStyleSelect styles={styleQ.data ?? []} value={styleKey} sample={sample} readOnly={viewer} onChange={(k) => setStyle.mutate(k)} />
          </InspectorSection>
          <div id="kho-am-thanh">
            <InspectorSection title="Nhạc và hiệu ứng">
              <SoundLibrary projectId={pid} sounds={soundsQ.data ?? []} readOnly={viewer} usedIds={usedSoundIds} />
            </InspectorSection>
          </div>
        </aside>
      </div>

      {planOpen ? (
        <DurationPlanDialog
          open
          seqId={sid}
          readOnly={viewer}
          onClose={() => setPlanOpen(false)}
          onApplied={(n) => {
            setPlanOpen(false);
            m.refresh();
            toast({ tone: 'success', message: `Đã khớp thời lượng ${n} clip theo giọng.`, undo: () => void callOpLoose('history.undo', { project_id: pid }).then(() => m.refresh()) });
          }}
        />
      ) : null}
    </div>
  );
}
