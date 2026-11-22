import { useCallback, useEffect, useRef, useState } from 'react';
import { emptyProject, useStore } from '../lib/store';
import { restoreBrowserMedia } from '../lib/browserMedia';
import { validateProject } from '../lib/validate';
import { exportJobs } from '../lib/exportJobs';
import { forgetStudioKey, rememberStudioKey, studioKey, studioRequest, uploadStudioFile } from '../lib/studioApi';
import type { Project } from '../lib/types';
import StudioLibrary from './StudioLibrary';
import StudioSubtitles from './StudioSubtitles';
import StudioVoice from './StudioVoice';
import type { StudioJob, StudioProjectRecord, StudioRender, StudioVoiceover } from './studioTypes';
import './studio.css';

type Tab = 'library' | 'subtitles' | 'voice' | 'exports';

function projectSnapshot(project: Project): string {
  return JSON.stringify({ ...project, media: project.media.map((item) => ({
    ...item, thumb: undefined, peaks: undefined, offline: undefined,
  })) });
}

export default function StudioWorkspace() {
  const [open, setOpen] = useState(true);
  const [tab, setTab] = useState<Tab>('library');
  const [inputKey, setInputKey] = useState(studioKey);
  const [connected, setConnected] = useState(false);
  const [checkingAccess, setCheckingAccess] = useState(true);
  const [localAccess, setLocalAccess] = useState(false);
  const [error, setError] = useState('');
  const [jobs, setJobs] = useState<StudioJob[]>([]);
  const [projects, setProjects] = useState<StudioProjectRecord[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [renders, setRenders] = useState<StudioRender[]>([]);
  const [selectedRenderId, setSelectedRenderId] = useState<string | null>(null);
  const [voiceovers, setVoiceovers] = useState<StudioVoiceover[]>([]);
  const [saveState, setSaveState] = useState('');
  const [uploading, setUploading] = useState(false);
  const activeIdRef = useRef<string | null>(null);
  const revisionRef = useRef(0);
  const savedRef = useRef('');
  const savingRef = useRef(false);
  const creatingRef = useRef<Promise<string> | null>(null);
  const saveTimer = useRef<number | null>(null);
  const searchRef = useRef('');
  const offsetRef = useRef(0);
  const selectedRender = renders.find((item) => item.id === selectedRenderId) ?? renders[0] ?? null;

  const reportError = useCallback((message: string) => setError(message.replace(/^Error: /, '')), []);
  const refresh = useCallback(async (search = searchRef.current, nextPage = false) => {
    searchRef.current = search;
    const offset = nextPage ? offsetRef.current + 50 : 0;
    try {
      const [nextJobs, nextProjects] = await Promise.all([
        studioRequest<StudioJob[]>(`/studio/library?offset=${offset}&limit=50&search=${encodeURIComponent(search)}`),
        studioRequest<StudioProjectRecord[]>('/studio/projects?limit=100'),
      ]);
      offsetRef.current = offset;
      setJobs((old) => nextPage ? [...old, ...nextJobs] : nextJobs);
      setProjects(nextProjects);
      setError('');
    } catch (cause) { reportError(String(cause)); }
  }, [reportError]);

  const refreshProjectData = useCallback(async (id: string) => {
    try {
      const [nextRenders, nextVoiceovers] = await Promise.all([
        studioRequest<StudioRender[]>(`/studio/projects/${id}/renders`),
        studioRequest<StudioVoiceover[]>(`/studio/projects/${id}/voiceovers`),
      ]);
      setRenders(nextRenders);
      setSelectedRenderId(nextRenders[0]?.id ?? null);
      setVoiceovers(nextVoiceovers);
    } catch (cause) { reportError(String(cause)); }
  }, [reportError]);

  const connect = useCallback(async (key: string) => {
    try {
      const response = await fetch('/studio/session', { method: 'POST', headers: { 'X-API-Key': key }, credentials: 'same-origin' });
      if (!response.ok) throw new Error('Неверный ключ Studio.');
      rememberStudioKey(key);
      setConnected(true);
      setError('');
      await refresh();
    } catch (cause) { reportError(String(cause)); setConnected(false); }
  }, [refresh, reportError]);

  useEffect(() => {
    void (async () => {
      try {
        const response = await fetch('/studio/session', { credentials: 'same-origin' });
        if (response.ok) {
          setLocalAccess(true);
          setConnected(true);
          setCheckingAccess(false);
          await refresh();
          return;
        }
      } catch { /* Key login remains available when local access cannot be checked. */ }
      const key = studioKey();
      if (key) await connect(key);
      setCheckingAccess(false);
    })();
    const toggle = () => setOpen((value) => !value);
    const showLibrary = () => { setTab('library'); setOpen(true); };
    window.addEventListener('studio-toggle', toggle);
    window.addEventListener('studio-open', showLibrary);
    return () => { window.removeEventListener('studio-toggle', toggle); window.removeEventListener('studio-open', showLibrary); };
  }, [connect, refresh]);

  const save = useCallback(async () => {
    const id = activeIdRef.current;
    if (!id) return true;
    if (savingRef.current) return false;
    const snapshot = projectSnapshot(useStore.getState().project);
    if (snapshot === savedRef.current) return true;
    savingRef.current = true;
    setSaveState('Сохранение…');
    let succeeded = false;
    try {
      const result = await studioRequest<StudioProjectRecord>(`/studio/projects/${id}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ document: JSON.parse(snapshot), expected_revision: revisionRef.current }),
      });
      revisionRef.current = result.revision;
      savedRef.current = snapshot;
      succeeded = true;
      setSaveState('Сохранено');
      setProjects((old) => old.map((row) => row.id === id ? result : row));
      setError('');
    } catch (cause) { setSaveState('Ошибка сохранения'); reportError(String(cause)); }
    finally {
      savingRef.current = false;
      if (succeeded && projectSnapshot(useStore.getState().project) !== savedRef.current && activeIdRef.current === id) {
        saveTimer.current = window.setTimeout(() => void save(), 1200);
      }
    }
    return succeeded;
  }, [reportError]);

  useEffect(() => {
    const unsubscribe = useStore.subscribe((state, previous) => {
      if (state.project === previous.project || !activeIdRef.current) return;
      if (saveTimer.current) window.clearTimeout(saveTimer.current);
      setSaveState('Изменения не сохранены');
      saveTimer.current = window.setTimeout(() => void save(), 900);
    });
    return () => { unsubscribe(); if (saveTimer.current) window.clearTimeout(saveTimer.current); };
  }, [save]);

  async function createProject() {
    try {
      if (!await save()) return;
      const document = { ...emptyProject(), name: 'Новый проект', width: 1080, height: 1920 };
      const created = await studioRequest<StudioProjectRecord>('/studio/projects', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ document }),
      });
      activeIdRef.current = created.id;
      revisionRef.current = created.revision;
      savedRef.current = projectSnapshot(document);
      useStore.getState().setProject(document);
      window.dispatchEvent(new Event('cw-project-loaded'));
      setActiveId(created.id);
      setRenders([]);
      setSelectedRenderId(null);
      setVoiceovers([]);
      setSaveState('Сохранено');
      await refresh();
    } catch (cause) { reportError(String(cause)); }
  }

  useEffect(() => {
    const create = () => { void createProject(); };
    const saveCurrent = () => { void save(); };
    window.addEventListener('studio-new', create);
    window.addEventListener('studio-save', saveCurrent);
    return () => { window.removeEventListener('studio-new', create); window.removeEventListener('studio-save', saveCurrent); };
  });

  async function openProject(id: string) {
    try {
      if (!await save()) return;
      const record = await studioRequest<StudioProjectRecord>(`/studio/projects/${id}`);
      const validation = validateProject(record.document);
      if (!validation.ok || !validation.project) throw new Error(validation.errors[0] || 'Повреждённый проект.');
      const restored = await restoreBrowserMedia(validation.project);
      activeIdRef.current = id;
      revisionRef.current = record.revision;
      savedRef.current = projectSnapshot(restored.project);
      useStore.getState().setProject(restored.project);
      window.dispatchEvent(new Event('cw-project-loaded'));
      setActiveId(id);
      setSaveState('Сохранено');
      await refreshProjectData(id);
      if (restored.missing.length) reportError(`Недоступны медиафайлы: ${restored.missing.join(', ')}`);
      else setError('');
    } catch (cause) { reportError(String(cause)); }
  }

  const ensureProject = useCallback(async () => {
    if (activeIdRef.current) return activeIdRef.current;
    if (creatingRef.current) return creatingRef.current;
    const pending = (async () => {
      const document = JSON.parse(projectSnapshot(useStore.getState().project));
      const record = await studioRequest<StudioProjectRecord>('/studio/projects', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ document }),
      });
      activeIdRef.current = record.id;
      revisionRef.current = record.revision;
      savedRef.current = projectSnapshot(document);
      setActiveId(record.id);
      await refresh();
      return record.id;
    })();
    creatingRef.current = pending;
    try { return await pending; }
    finally { creatingRef.current = null; }
  }, [refresh]);

  useEffect(() => {
    if (!connected) return;
    const unsubscribe = useStore.subscribe((state, previous) => {
      if (state.project === previous.project || activeIdRef.current) return;
      void ensureProject().then(() => save()).catch((cause) => reportError(String(cause)));
    });
    return unsubscribe;
  }, [connected, ensureProject, save, reportError]);

  useEffect(() => {
    const onExport = (event: Event) => {
      const file = (event as CustomEvent<{ file: File }>).detail.file;
      if (!file || !connected) return;
      void (async () => {
        setUploading(true);
        try {
          const id = await ensureProject();
          if (!await save()) throw new Error('Не удалось сохранить проект перед экспортом.');
          const item = await uploadStudioFile<StudioRender>(`/studio/projects/${id}/renders`, file);
          setRenders((old) => [item, ...old]);
          setSelectedRenderId(item.id);
          setTab('exports');
          setOpen(true);
        } catch (cause) { reportError(String(cause)); }
        finally { setUploading(false); }
      })();
    };
    window.addEventListener('cw-browser-export-complete', onExport);
    return () => window.removeEventListener('cw-browser-export-complete', onExport);
  }, [connected, ensureProject, save, reportError]);

  const updateRender = useCallback((item: StudioRender) => {
    setRenders((old) => old.map((row) => row.id === item.id ? item : row));
  }, []);
  const updateVoice = useCallback((item: StudioVoiceover) => {
    setVoiceovers((old) => [item, ...old.filter((row) => row.id !== item.id)]);
  }, []);

  if (!open) return null;
  return <aside className="studio-workspace" aria-label="Clip Studio">
    <header className="studio-head"><div><strong>Clip Studio</strong><span>{saveState}</span></div>
      <button aria-label="Закрыть панель Studio" title="Закрыть" onClick={() => setOpen(false)}>×</button></header>
    {checkingAccess ? <div className="studio-login" role="status">Подключение к Studio…</div> : !connected ? <form className="studio-login" onSubmit={(event) => { event.preventDefault(); void connect(inputKey); }}>
      <h2>Доступ к Studio</h2><label className="studio-field">API key<input type="password" autoComplete="off" value={inputKey}
        onChange={(event) => setInputKey(event.target.value)} /></label>
      <button className="primary" type="submit">Подключиться</button>
      {error && <p className="studio-error" role="alert">{error}</p>}
    </form> : <>
      <nav className="studio-tabs" aria-label="Разделы Studio">
        {([['library', 'Работы'], ['subtitles', 'Субтитры'], ['voice', 'Озвучка'], ['exports', 'Экспорт']] as const)
          .map(([id, label]) => <button key={id} aria-current={tab === id ? 'page' : undefined}
            className={tab === id ? 'active' : ''} onClick={() => setTab(id)}>{label}</button>)}
      </nav>
      <div className="studio-scroll">
        {error && <p className="studio-error" role="alert">{error}</p>}
        {tab === 'library' && <StudioLibrary jobs={jobs} projects={projects} activeId={activeId}
          onOpen={(id) => void openProject(id)} onNew={() => void createProject()} onRefresh={(search, next) => void refresh(search, next)} onError={reportError} />}
        {tab === 'subtitles' && <StudioSubtitles render={selectedRender} onRender={updateRender} onError={reportError} />}
        {tab === 'voice' && <StudioVoice render={selectedRender} voiceovers={voiceovers} onVoice={updateVoice} onError={reportError} />}
        {tab === 'exports' && <div className="studio-section">
          <div className="studio-section-head"><h2>Сборки</h2><span>{renders.length}</span></div>
          <button className="primary studio-wide" disabled={uploading}
            onClick={() => { try { void exportJobs.start({ preset: 'source' }); }
              catch (cause) { reportError(String(cause)); } }}>
            {uploading ? 'Сохранение MP4…' : 'Экспортировать монтаж'}
          </button>
          {renders.map((render) => <button key={render.id} className={`studio-item${selectedRender?.id === render.id ? ' active' : ''}`}
            onClick={() => setSelectedRenderId(render.id)}><strong>{new Date(render.created_at).toLocaleString('ru-RU')}</strong>
            <small>{render.duration.toFixed(1)} с · версия проекта {render.revision}</small></button>)}
          {selectedRender && <><video className="studio-video" controls preload="metadata" src={selectedRender.url} />
            <a className="studio-download" href={selectedRender.url} download>Скачать выбранный MP4</a></>}
        </div>}
      </div>
      <footer className="studio-footer"><span>{activeId ? `Проект ${activeId.slice(0, 8)}` : 'Проект не сохранён'}</span>
        {localAccess ? <span>Локальный доступ</span> : <button onClick={() => { void fetch('/studio/logout', { method: 'POST', credentials: 'same-origin' });
          forgetStudioKey(); setConnected(false); setInputKey(''); setOpen(true); }}>Забыть ключ</button>}</footer>
    </>}
  </aside>;
}
