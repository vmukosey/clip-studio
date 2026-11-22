import { useEffect, useState } from 'react';
import { useStore, uid } from '../lib/store';
import { studioRequest, type StudioAsset } from '../lib/studioApi';
import type { StudioRender, StudioVoiceover } from './studioTypes';

type Region = { start: number; end: number };
type Line = Region & { text: string };
const processing = new Set(['queued', 'analyzing', 'approved', 'rendering']);

export default function StudioVoice({ render, voiceovers, onVoice, onError }:
  { render: StudioRender | null; voiceovers: StudioVoiceover[];
    onVoice: (voice: StudioVoiceover) => void; onError: (message: string) => void }) {
  const [selected, setSelected] = useState<string | null>(null);
  const [mode, setMode] = useState('description');
  const [prompt, setPrompt] = useState('');
  const [regions, setRegions] = useState<Region[]>([]);
  const [lines, setLines] = useState<Line[]>([]);
  const [busy, setBusy] = useState(false);
  const voice = voiceovers.find((item) => item.id === selected) ?? voiceovers[0] ?? null;
  const duration = render?.duration ?? 0;
  const playhead = useStore((state) => state.playhead);

  useEffect(() => {
    if (!voice) return;
    setSelected(voice.id);
    setLines(voice.lines);
  }, [voice?.id, voice?.status, JSON.stringify(voice?.lines)]);

  useEffect(() => {
    if (!voice) return;
    setMode(voice.mode);
    setPrompt(voice.prompt || '');
    setRegions(voice.regions || []);
  }, [voice?.id]);

  useEffect(() => {
    if (!voice || !processing.has(voice.status)) return;
    const timer = window.setInterval(async () => {
      try { onVoice(await studioRequest<StudioVoiceover>(`/voiceovers/${voice.id}`)); }
      catch (error) { onError(String(error)); }
    }, 4000);
    return () => window.clearInterval(timer);
  }, [voice?.id, voice?.status, onVoice, onError]);

  function setRegion(index: number, field: keyof Region, value: number) {
    setRegions((old) => old.map((row, i) => i === index ? { ...row, [field]: value } : row));
  }

  async function create(manual: boolean) {
    if (!render) return;
    setBusy(true);
    try {
      const next = await studioRequest<StudioVoiceover>('/voiceovers', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ render_id: render.id, mode, prompt, regions, manual }),
      });
      onVoice(next);
      setSelected(next.id);
      setLines([]);
    } catch (error) { onError(String(error)); }
    finally { setBusy(false); }
  }

  async function saveLines(): Promise<StudioVoiceover | null> {
    if (!voice) return null;
    const saved = await studioRequest<StudioVoiceover>(`/voiceovers/${voice.id}/lines`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ lines }),
    });
    onVoice(saved);
    return saved;
  }

  async function approve() {
    if (!voice) return;
    setBusy(true);
    try {
      await saveLines();
      onVoice(await studioRequest<StudioVoiceover>(`/voiceovers/${voice.id}/approve`, { method: 'POST' }));
    } catch (error) { onError(String(error)); }
    finally { setBusy(false); }
  }

  async function importTrack() {
    if (!voice || !render) return;
    setBusy(true);
    try {
      const asset = await studioRequest<StudioAsset>(`/studio/assets/from-voiceover/${voice.id}`, { method: 'POST' });
      const mediaId = uid();
      useStore.getState().addMedia({ id: mediaId, name: asset.name, kind: 'audio', src: asset.url,
                                    duration: render.duration, color: '#e3ac68' });
      useStore.getState().addTrack('audio');
      const state = useStore.getState();
      const track = Object.keys(state.project.trackSettings ?? {}).filter((id) => /^a\d+$/.test(id))
        .sort((a, b) => Number(b.slice(1)) - Number(a.slice(1)))[0] ?? 'a2';
      state.addClipFromMedia(mediaId);
      const placed = useStore.getState().project.clips.find((item) => item.mediaId === mediaId);
      if (placed) useStore.getState().updateClip(placed.id, { trackId: track, start: 0 });
    } catch (error) { onError(String(error)); }
    finally { setBusy(false); }
  }

  const editable = voice && ['draft', 'needs_revision', 'failed', 'complete'].includes(voice.status);
  return <div className="studio-section">
    <div className="studio-section-head"><h2>Озвучка</h2><span>{voiceovers.length} черновиков</span></div>
    {!render && <p className="studio-empty">Сначала экспортируйте текущий монтаж.</p>}
    {render && <>
      <label className="studio-field">Режим
        <select value={mode} onChange={(event) => setMode(event.target.value)}>
          <option value="description">Аудиодескрипция</option>
          <option value="commentary">Комментарий</option>
          <option value="intro">Вступление</option>
        </select>
      </label>
      <label className="studio-field">Промпт для текста озвучки
        <textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} maxLength={2000}
          placeholder="Что подчеркнуть, какой тон использовать…" />
      </label>
      <div className="studio-section-head"><h3>Интервалы</h3>
        <button onClick={() => setRegions((old) => [...old, { start: Math.min(playhead, Math.max(0, duration - 0.5)),
          end: Math.min(duration, Math.max(playhead + 2, 0.5)) }])}>Добавить</button></div>
      {regions.map((region, index) => <div className="studio-region" key={index}>
        <div className="studio-region-bar"><input aria-label={`Начало интервала ${index + 1}`} type="range" min="0" max={duration}
          step="0.01" value={region.start} onChange={(event) => setRegion(index, 'start', Number(event.target.value))} />
          <input aria-label={`Конец интервала ${index + 1}`} type="range" min="0" max={duration}
            step="0.01" value={region.end} onChange={(event) => setRegion(index, 'end', Number(event.target.value))} /></div>
        <div className="studio-row"><label>Начало, с<input type="number" min="0" max={duration} step="0.01" value={region.start}
          onChange={(event) => setRegion(index, 'start', Number(event.target.value))} /></label>
          <label>Конец, с<input type="number" min="0" max={duration} step="0.01" value={region.end}
            onChange={(event) => setRegion(index, 'end', Number(event.target.value))} /></label>
          <button aria-label={`Удалить интервал ${index + 1}`} title="Удалить интервал" onClick={() => setRegions((old) => old.filter((_, i) => i !== index))}>×</button></div>
        <div className="studio-row"><button onClick={() => setRegion(index, 'start', playhead)}>Начало = курсор</button>
          <button onClick={() => setRegion(index, 'end', playhead)}>Конец = курсор</button></div>
      </div>)}
      <div className="studio-row"><button className="primary" disabled={busy} onClick={() => void create(false)}>Создать черновик ИИ</button>
        <button disabled={busy} onClick={() => void create(true)}>Написать вручную</button></div>
    </>}
    {voiceovers.length > 0 && <>
      <div className="studio-divider" />
      <label className="studio-field">Черновик
        <select value={voice?.id ?? ''} onChange={(event) => setSelected(event.target.value)}>
          {voiceovers.map((item) => <option key={item.id} value={item.id}>
            {new Date(item.created_at).toLocaleString('ru-RU')} · {item.status}
          </option>)}
        </select>
      </label>
      {voice && <>
        <p className="studio-note">Статус: {voice.status}{voice.render_id !== render?.id ? ' · основан на другой сборке' : ''}</p>
        {voice.error && <p className="studio-error">{voice.error}</p>}
        <div className="studio-lines">{lines.map((line, index) => <div className="studio-line" key={index}>
          <div className="studio-row"><label>Начало, с<input type="number" min="0" step="0.01" value={line.start} disabled={!editable}
            onChange={(event) => setLines((old) => old.map((row, i) => i === index ? { ...row, start: Number(event.target.value) } : row))} /></label>
            <label>Конец, с<input type="number" min="0" step="0.01" value={line.end} disabled={!editable}
              onChange={(event) => setLines((old) => old.map((row, i) => i === index ? { ...row, end: Number(event.target.value) } : row))} /></label>
            <button title="Удалить реплику" aria-label={`Удалить реплику ${index + 1}`} disabled={!editable}
              onClick={() => setLines((old) => old.filter((_, i) => i !== index))}>×</button></div>
          <textarea aria-label={`Текст реплики ${index + 1}`} value={line.text} disabled={!editable}
            onChange={(event) => setLines((old) => old.map((row, i) => i === index ? { ...row, text: event.target.value } : row))} />
        </div>)}</div>
        <div className="studio-row"><button disabled={!editable} onClick={() => setLines((old) => [...old,
          { start: regions[0]?.start ?? Math.min(playhead, Math.max(0, duration - 2)),
            end: regions[0]?.end ?? Math.min(duration, playhead + 2), text: '' }])}>Добавить реплику</button>
          <button disabled={!editable || busy} onClick={() => void saveLines().catch((error) => onError(String(error)))}>Сохранить текст</button></div>
        <button className="primary studio-wide" disabled={!editable || busy || !lines.length} onClick={() => void approve()}>Утвердить и озвучить</button>
        {voice.artifacts.wav && <><audio controls src={voice.artifacts.wav} />
          <div className="studio-row"><button disabled={busy} onClick={() => void importTrack()}>Добавить WAV на таймлайн</button>
            <a href={voice.artifacts.mp4} download>Скачать MP4</a></div></>}
      </>}
    </>}
  </div>;
}
