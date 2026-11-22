import { useEffect, useState } from 'react';
import { useStore, uid } from '../lib/store';
import { studioRequest } from '../lib/studioApi';
import type { StudioRender } from './studioTypes';

export default function StudioSubtitles({ render, onRender, onError }:
  { render: StudioRender | null; onRender: (render: StudioRender) => void; onError: (message: string) => void }) {
  const [working, setWorking] = useState(false);
  const count = useStore((state) => state.project.subtitles.length);

  useEffect(() => {
    if (!render || !['queued', 'running'].includes(render.transcript_status)) return;
    const timer = window.setInterval(async () => {
      try { onRender(await studioRequest<StudioRender>(`/studio/renders/${render.id}`)); }
      catch (error) { onError(String(error)); }
    }, 3000);
    return () => window.clearInterval(timer);
  }, [render?.id, render?.transcript_status, onRender, onError]);

  async function generate() {
    if (!render) return;
    setWorking(true);
    try { onRender(await studioRequest<StudioRender>(`/studio/renders/${render.id}/subtitles`, { method: 'POST' })); }
    catch (error) { onError(String(error)); }
    finally { setWorking(false); }
  }

  function apply(replace: boolean) {
    if (!render) return;
    const state = useStore.getState();
    const cues = render.transcript.map((cue) => ({ ...cue, id: `sub-${uid()}` }));
    state.setProject({ ...state.project,
      subtitles: replace ? cues : [...state.project.subtitles, ...cues] });
    window.dispatchEvent(new Event('studio-open-captions'));
  }

  return <div className="studio-section">
    <div className="studio-section-head"><h2>Субтитры</h2><span>{count} в проекте</span></div>
    <button className="studio-wide" onClick={() => window.dispatchEvent(new Event('studio-open-captions'))}>
      Открыть дорожку субтитров
    </button>
    <div className="studio-divider" />
    <h3>Распознавание сборки</h3>
    {!render && <p className="studio-empty">Сначала экспортируйте текущий монтаж. Он появится в истории сборок.</p>}
    {render && <>
      <p className="studio-note">Сборка {new Date(render.created_at).toLocaleString('ru-RU')} · {render.duration.toFixed(1)} с</p>
      <button className="primary studio-wide" onClick={() => void generate()} disabled={working || ['queued', 'running'].includes(render.transcript_status)}>
        {['queued', 'running'].includes(render.transcript_status) ? 'Распознаётся…' : 'Распознать речь'}
      </button>
      {render.transcript_error && <p className="studio-error">{render.transcript_error}</p>}
      {render.transcript_status === 'complete' && <>
        <p className="studio-note">Найдено фраз: {render.transcript.length}</p>
        <div className="studio-cues">{render.transcript.map((cue, index) => <p key={index}>
          <time>{cue.start.toFixed(1)}–{cue.end.toFixed(1)} с</time> {cue.text}
        </p>)}</div>
        <div className="studio-row"><button onClick={() => apply(false)}>Добавить к текущим</button>
          <button onClick={() => apply(true)}>Заменить текущие</button></div>
      </>}
    </>}
  </div>;
}
