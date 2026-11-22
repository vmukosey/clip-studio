import { useState } from 'react';
import { useStore, uid } from '../lib/store';
import { srtToSubtitles } from '../lib/srt';
import { studioRequest, type StudioAsset } from '../lib/studioApi';
import type { StudioJob, StudioProjectRecord } from './studioTypes';

export default function StudioLibrary({ jobs, projects, activeId, onOpen, onNew, onRefresh, onError }:
  { jobs: StudioJob[]; projects: StudioProjectRecord[]; activeId: string | null;
    onOpen: (id: string) => void; onNew: () => void; onRefresh: (search?: string, nextPage?: boolean) => void;
    onError: (message: string) => void }) {
  const [search, setSearch] = useState('');
  const [busy, setBusy] = useState('');

  async function addClip(clip: StudioJob['clips'][number]) {
    setBusy(clip.id);
    try {
      const asset = await studioRequest<StudioAsset>(`/studio/assets/from-clip/${clip.id}`, { method: 'POST' });
      const state = useStore.getState();
      const id = `clip-${clip.id}-${uid()}`;
      state.addMedia({ id, name: asset.name, kind: 'video', src: asset.url,
                       duration: clip.duration, color: '#50bdb5' });
      useStore.getState().addClipFromMedia(id);
    } catch (error) { onError(String(error)); }
    finally { setBusy(''); }
  }

  async function addCaptions(clip: StudioJob['clips'][number]) {
    setBusy(clip.id);
    try {
      const state = useStore.getState();
      const media = state.project.media.find((item) => item.id.startsWith(`clip-${clip.id}-`) && item.kind === 'video');
      const placed = state.project.clips.find((item) => item.mediaId === media?.id);
      if (!placed) throw new Error('Сначала добавьте этот клип на таймлайн.');
      const response = await fetch(`/clips/${clip.id}/artifacts/srt`, { credentials: 'same-origin' });
      if (!response.ok) throw new Error('Файл субтитров недоступен.');
      const subtitles = srtToSubtitles(await response.text()).map((item) => ({
        ...item, id: `sub-${uid()}`, start: item.start + placed.start, end: item.end + placed.start,
      }));
      state.setProject({ ...state.project, subtitles: [...state.project.subtitles, ...subtitles] });
    } catch (error) { onError(String(error)); }
    finally { setBusy(''); }
  }

  return <div className="studio-section">
    <div className="studio-section-head"><h2>Проекты</h2><button className="primary" onClick={onNew}>Новый проект</button></div>
    <div className="studio-list">
      {projects.map((project) => <button key={project.id} className={`studio-item${activeId === project.id ? ' active' : ''}`}
        onClick={() => onOpen(project.id)}><strong>{project.name}</strong><small>{new Date(project.updated_at).toLocaleString('ru-RU')}</small></button>)}
      {!projects.length && <p className="studio-empty">Проектов пока нет.</p>}
    </div>
    <div className="studio-section-head"><h2>Старые работы</h2><button onClick={() => onRefresh(search)}>Обновить</button></div>
    <form className="studio-search" onSubmit={(event) => { event.preventDefault(); onRefresh(search); }}>
      <input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Название или ID задачи" aria-label="Поиск работ" />
      <button type="submit">Найти</button>
    </form>
    <div className="studio-list">
      {jobs.map((job) => <section className="studio-job" key={job.id}>
        <h3 title={job.source}>{job.source.split(/[\\/]/).at(-1) || job.id}</h3>
        <small>{new Date(job.created_at).toLocaleDateString('ru-RU')} · {job.status}</small>
        {job.clips.map((clip) => <div className="studio-clip" key={clip.id}>
          <span><strong>{clip.title || 'Клип'}</strong><small>{clip.duration.toFixed(1)} с · {clip.available ? 'Файл доступен' : 'Файл отсутствует'}</small></span>
          <div className="studio-clip-actions">
            <button disabled={!clip.available || busy === clip.id} onClick={() => void addClip(clip)}>В монтаж</button>
            {clip.srt && <button disabled={busy === clip.id} onClick={() => void addCaptions(clip)}>SRT</button>}
          </div>
        </div>)}
      </section>)}
      {!jobs.length && <p className="studio-empty">Работы не найдены.</p>}
    </div>
    {jobs.length >= 50 && <button className="studio-more" onClick={() => onRefresh(search, true)}>Показать ещё</button>}
  </div>;
}
