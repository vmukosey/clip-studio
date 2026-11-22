"""Durable media library and CutWright project storage for the local Studio."""

import json
import shutil
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Cookie, Depends, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import or_, select

from .config import settings
from .db import Clip, HiddenItem, Job, SessionLocal, Source, StudioAsset, StudioProject, StudioRender, Voiceover
from .export_names import safe_name
from .media import probe_video, require_free_space
from .transcription import transcribe


def studio_auth(x_api_key: str | None = Header(default=None),
                clip_studio_key: str | None = Cookie(default=None)):
    if x_api_key != settings.clip_api_key and clip_studio_key != settings.clip_api_key:
        raise HTTPException(401, 'studio session required')


router = APIRouter(prefix='/studio', dependencies=[Depends(studio_auth)])
root = settings.clip_storage_root / 'studio'


class ProjectCreate(BaseModel):
    document: dict = Field(default_factory=dict)


class ProjectUpdate(BaseModel):
    document: dict
    expected_revision: int = Field(ge=1)


def project_view(item: StudioProject, include_document: bool = False) -> dict:
    result = {'id': item.id, 'name': item.name, 'revision': item.revision,
              'created_at': item.created_at.isoformat(), 'updated_at': item.updated_at.isoformat()}
    if include_document:
        result['document'] = item.document
    return result


def valid_document(document: dict):
    if not isinstance(document, dict) or document.get('version') != 1:
        raise HTTPException(422, 'expected a CutWright version 1 project')
    if not isinstance(document.get('media'), list) or not isinstance(document.get('clips'), list):
        raise HTTPException(422, 'project media and clips must be arrays')
    if len(json.dumps(document, ensure_ascii=False)) > 4_000_000:
        raise HTTPException(413, 'project is too large')
    for media in document['media']:
        if not isinstance(media, dict):
            raise HTTPException(422, 'invalid media entry')
        src = media.get('src', '')
        if src and not (isinstance(src, str) and src.startswith('/studio/assets/')
                        and src.endswith('/file')):
            raise HTTPException(422, 'import media into the Studio library before saving')


@router.get('/library')
def library(offset: int = 0, limit: int = 50, search: str = '', visibility: str = 'visible'):
    if visibility not in {'visible', 'hidden'}:
        raise HTTPException(422, 'visibility must be visible or hidden')
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)
    with SessionLocal() as db:
        hidden_jobs = select(HiddenItem.entity_id).where(HiddenItem.entity_type == 'job')
        hidden_clips = select(HiddenItem.entity_id).where(HiddenItem.entity_type == 'clip')
        query = select(Job, Source).join(Source, Source.id == Job.source_id)
        hidden_job_clips = select(Clip.job_id).where(Clip.id.in_(hidden_clips), Clip.job_id == Job.id).exists()
        query = query.where((Job.id.in_(hidden_jobs) | hidden_job_clips) if visibility == 'hidden' else ~Job.id.in_(hidden_jobs))
        if search.strip():
            matching_clip = select(Clip.id).where(Clip.job_id == Job.id,
                                                  Clip.title.contains(search.strip())).exists()
            query = query.where(or_(Job.id.contains(search.strip()),
                                    Source.uri.contains(search.strip()), matching_clip))
        rows = db.execute(query.order_by(Job.created_at.desc()).offset(offset).limit(limit)).all()
        result = []
        for job, source in rows:
            clips_query = select(Clip).where(Clip.job_id == job.id)
            clips_query = clips_query.where(Clip.id.in_(hidden_clips) if visibility == 'hidden' else ~Clip.id.in_(hidden_clips))
            clips = db.scalars(clips_query.order_by(Clip.start)).all()
            if visibility == 'hidden' and not clips and not db.scalar(select(HiddenItem.id).where(HiddenItem.entity_type == 'job', HiddenItem.entity_id == job.id)):
                continue
            result.append({'id': job.id, 'status': job.status, 'source': source.uri,
                 'created_at': job.created_at.isoformat(),
                 'clips': [{'id': clip.id, 'title': clip.title, 'status': clip.status,
                            'duration': round(clip.end - clip.start, 2),
                            'available': bool((clip.artifacts or {}).get('mp4') and
                                              Path(clip.artifacts['mp4']).is_file()),
                            'srt': bool((clip.artifacts or {}).get('srt') and
                                        Path(clip.artifacts['srt']).is_file())}
                           for clip in clips]})
        return result


class VisibilityUpdate(BaseModel):
    hidden: bool


@router.patch('/library/{entity_type}/{entity_id}/visibility')
def set_visibility(entity_type: str, entity_id: str, payload: VisibilityUpdate):
    if entity_type not in {'job', 'clip'}:
        raise HTTPException(422, 'entity_type must be job or clip')
    with SessionLocal() as db:
        model = Job if entity_type == 'job' else Clip
        if not db.get(model, entity_id):
            raise HTTPException(404, f'{entity_type} not found')
        item = db.scalar(select(HiddenItem).where(HiddenItem.entity_type == entity_type,
                                                  HiddenItem.entity_id == entity_id))
        if payload.hidden and not item:
            db.add(HiddenItem(id=uuid4().hex, entity_type=entity_type, entity_id=entity_id))
        elif not payload.hidden and item:
            db.delete(item)
        db.commit()
    return {'entity_type': entity_type, 'entity_id': entity_id, 'hidden': payload.hidden}


@router.get('/projects')
def list_projects(offset: int = 0, limit: int = 50):
    with SessionLocal() as db:
        rows = db.scalars(select(StudioProject).order_by(StudioProject.updated_at.desc())
                          .offset(max(0, offset)).limit(min(max(limit, 1), 100))).all()
        return [project_view(row) for row in rows]


@router.post('/projects')
def create_project(payload: ProjectCreate):
    document = payload.document or {'version': 1, 'name': 'Новый проект', 'width': 1080,
                                    'height': 1920, 'fps': 30, 'media': [], 'clips': [],
                                    'transitions': [], 'subtitles': [], 'customTransitions': []}
    valid_document(document)
    item = StudioProject(id=uuid4().hex, name=str(document.get('name') or 'Новый проект')[:200],
                         document=document, revision=1)
    with SessionLocal() as db:
        db.add(item)
        db.commit()
        return project_view(item, True)


@router.get('/projects/{project_id}')
def get_project(project_id: str):
    with SessionLocal() as db:
        item = db.get(StudioProject, project_id)
        if not item:
            raise HTTPException(404, 'project not found')
        return project_view(item, True)


@router.put('/projects/{project_id}')
def update_project(project_id: str, payload: ProjectUpdate):
    valid_document(payload.document)
    with SessionLocal() as db:
        item = db.get(StudioProject, project_id)
        if not item:
            raise HTTPException(404, 'project not found')
        if item.revision != payload.expected_revision:
            raise HTTPException(409, 'project changed in another tab; reload before saving')
        item.document = payload.document
        item.name = str(payload.document.get('name') or 'Новый проект')[:200]
        item.revision += 1
        db.commit()
        return project_view(item, True)


def asset_view(item: StudioAsset) -> dict:
    return {'id': item.id, 'name': item.name, 'kind': item.kind,
            'url': f'/studio/assets/{item.id}/file', 'source_clip_id': item.source_clip_id}


def asset_target(asset_id: str, filename: str) -> Path:
    original = Path(filename.replace('\\', '/')).name
    suffix = Path(original).suffix.lower()
    return root / 'assets' / asset_id / f'{safe_name(Path(original).stem, "media", 180)}{suffix}'


@router.post('/assets/upload')
async def upload_asset(file: UploadFile = File(...)):
    filename = file.filename or ''
    suffix = Path(filename).suffix.lower()
    kind = ('video' if suffix in {'.mp4', '.mov', '.mkv', '.webm'} else
            'audio' if suffix in {'.mp3', '.wav', '.m4a', '.aac', '.flac'} else
            'image' if suffix in {'.png', '.jpg', '.jpeg', '.webp'} else None)
    if not kind:
        raise HTTPException(415, 'unsupported media format')
    asset_id = uuid4().hex
    target = asset_target(asset_id, filename)
    target.parent.mkdir(parents=True, exist_ok=True)
    require_free_space(target.parent)
    size = 0
    try:
        with target.open('xb') as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > settings.clip_max_upload_mb * 1024 * 1024:
                    raise HTTPException(413, 'upload exceeds configured size limit')
                output.write(chunk)
        if kind == 'video':
            probe_video(target)
        if not size:
            raise HTTPException(422, 'empty media file')
    except Exception:
        target.unlink(missing_ok=True)
        raise
    item = StudioAsset(id=asset_id, name=target.name, kind=kind, path=str(target))
    with SessionLocal() as db:
        db.add(item)
        db.commit()
    return asset_view(item)


def copy_asset(source: Path, name: str, kind: str, clip_id: str | None = None) -> dict:
    if not source.is_file():
        raise HTTPException(404, 'media file is missing')
    asset_id = uuid4().hex
    target = asset_target(asset_id, name)
    target.parent.mkdir(parents=True, exist_ok=True)
    require_free_space(target.parent, max(1, source.stat().st_size // (1024 * 1024)))
    shutil.copy2(source, target)
    item = StudioAsset(id=asset_id, name=target.name, kind=kind, path=str(target),
                       source_clip_id=clip_id)
    with SessionLocal() as db:
        db.add(item)
        db.commit()
    return asset_view(item)


@router.post('/assets/from-clip/{clip_id}')
def asset_from_clip(clip_id: str):
    with SessionLocal() as db:
        clip = db.get(Clip, clip_id)
        path = (clip.artifacts or {}).get('mp4') if clip else None
        title = clip.title if clip else ''
    if not path:
        raise HTTPException(404, 'exported clip not found')
    return copy_asset(Path(path), f'{title or clip_id}.mp4', 'video', clip_id)


@router.post('/assets/from-voiceover/{voiceover_id}')
def asset_from_voiceover(voiceover_id: str):
    with SessionLocal() as db:
        voice = db.get(Voiceover, voiceover_id)
        path = (voice.artifacts or {}).get('wav') if voice else None
    if not path:
        raise HTTPException(404, 'voice track not found')
    return copy_asset(Path(path), f'Озвучка-{voiceover_id[:8]}.wav', 'audio')


@router.get('/assets/{asset_id}/file')
def asset_file(asset_id: str):
    with SessionLocal() as db:
        item = db.get(StudioAsset, asset_id)
        path = Path(item.path) if item else None
    if not path or not path.is_file():
        raise HTTPException(404, 'media file is missing')
    return FileResponse(path, filename=item.name, content_disposition_type='inline')


def render_view(item: StudioRender) -> dict:
    return {'id': item.id, 'project_id': item.project_id, 'revision': item.revision,
            'duration': item.duration,
            'url': f'/studio/renders/{item.id}/file', 'transcript_status': item.transcript_status,
            'transcript': item.transcript or [], 'transcript_error': item.transcript_error,
            'created_at': item.created_at.isoformat()}


@router.post('/projects/{project_id}/renders')
async def upload_render(project_id: str, file: UploadFile = File(...)):
    with SessionLocal() as db:
        project = db.get(StudioProject, project_id)
        if not project:
            raise HTTPException(404, 'project not found')
        revision = project.revision
    render_id = uuid4().hex
    target = root / 'projects' / project_id / 'renders' / f'{render_id}.mp4'
    target.parent.mkdir(parents=True, exist_ok=True)
    require_free_space(target.parent)
    size = 0
    try:
        with target.open('xb') as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > settings.clip_max_upload_mb * 1024 * 1024:
                    raise HTTPException(413, 'render exceeds configured size limit')
                output.write(chunk)
        if not size:
            raise HTTPException(422, 'empty render')
        probe = probe_video(target)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    item = StudioRender(id=render_id, project_id=project_id, revision=revision,
                        duration=probe['duration'], path=str(target))
    with SessionLocal() as db:
        db.add(item)
        db.commit()
    return render_view(item)


@router.get('/projects/{project_id}/renders')
def list_renders(project_id: str):
    with SessionLocal() as db:
        rows = db.scalars(select(StudioRender).where(StudioRender.project_id == project_id)
                          .order_by(StudioRender.created_at.desc())).all()
        return [render_view(row) for row in rows]


@router.get('/renders/{render_id}')
def get_render(render_id: str):
    with SessionLocal() as db:
        item = db.get(StudioRender, render_id)
        if not item:
            raise HTTPException(404, 'render not found')
        return render_view(item)


@router.get('/renders/{render_id}/file')
def render_file(render_id: str):
    with SessionLocal() as db:
        item = db.get(StudioRender, render_id)
        path = Path(item.path) if item else None
    if not path or not path.is_file():
        raise HTTPException(404, 'render file is missing')
    return FileResponse(path, media_type='video/mp4', filename=path.name,
                        content_disposition_type='inline')


def transcribe_render(render_id: str):
    with SessionLocal() as db:
        item = db.get(StudioRender, render_id)
        if not item or item.transcript_status != 'queued':
            return
        item.transcript_status = 'running'
        db.commit()
        try:
            segments = transcribe(Path(item.path), settings.clip_language,
                                  Path(item.path).with_suffix('.transcript.json'))
            item.transcript = [{'start': round(float(row['start']), 3),
                                'end': round(float(row['end']), 3), 'text': row['text'].strip()}
                               for row in segments if row['text'].strip()]
            item.transcript_status = 'complete'
            item.transcript_error = None
        except Exception as exc:
            item.transcript_status = 'failed'
            item.transcript_error = str(exc)[:1000]
        db.commit()


@router.post('/renders/{render_id}/subtitles')
def request_subtitles(render_id: str, background_tasks: BackgroundTasks):
    with SessionLocal() as db:
        item = db.get(StudioRender, render_id)
        if not item:
            raise HTTPException(404, 'render not found')
        if item.transcript_status in {'idle', 'failed'}:
            item.transcript_status = 'queued'
            item.transcript_error = None
            db.commit()
            background_tasks.add_task(transcribe_render, render_id)
        return render_view(item)
