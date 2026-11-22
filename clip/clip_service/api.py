from contextlib import asynccontextmanager
from typing import Annotated
from uuid import uuid4

from pathlib import Path

from fastapi import BackgroundTasks, Cookie, Depends, FastAPI, File, Header, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from .config import settings
from .db import Clip, Job, SessionLocal, Source, Voiceover, init_db
from .gpu import AcceleratorError, preflight, resolve_acceleration
from .media import MediaError, prepare_source, probe_video, require_free_space, run, upload_target
from .schemas import CampaignCreate, FinalizeRequest, JobCreate, ManualInterval, ReviewAction, SourceCreate, TitleUpdate, VoiceoverCreate, VoiceoverEdit, SelectionUpdate
from .service import (add_interval, clip_view, create_campaign, create_job, create_source,
                      decisions, export_clip, finalize_cutwright, job_view, prepare_working_copy, review_clip, rename_clip, choose_clip)
from .tasks import run_job, voiceover_draft, voiceover_render
from . import voiceover
from .studio_projects import router as studio_router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    if not settings.clip_voiceover_queue:
        with SessionLocal() as db:
            for item in db.scalars(select(Voiceover).where(
                    Voiceover.status.in_(['analyzing', 'rendering']))):
                item.status = 'failed'
                item.error = 'interrupted local voiceover task; retry the draft or approve the saved lines again'
            db.commit()
    yield

app = FastAPI(title='Clip Service', version='1.0', lifespan=lifespan)
app.include_router(studio_router)
studio_web = Path(__file__).with_name('studio_web')
app.mount('/studio/static', StaticFiles(directory=studio_web / 'static', check_dir=False), name='studio-static')


def dispatch_voiceover(task, local_function, voiceover_id: str, background_tasks: BackgroundTasks):
    if settings.clip_voiceover_queue:
        try:
            task.delay(voiceover_id)
            return
        except Exception:
            pass
    background_tasks.add_task(local_function, voiceover_id)

def auth(x_api_key: str | None = Header(default=None),
         clip_studio_key: str | None = Cookie(default=None)):
    if x_api_key != settings.clip_api_key and clip_studio_key != settings.clip_api_key:
        raise HTTPException(401, 'invalid api key')

@app.get('/studio')
def studio():
    return RedirectResponse('/studio/')


@app.get('/studio/')
def studio_index():
    return FileResponse(studio_web / 'index.html' if (studio_web / 'index.html').is_file()
                        else Path(__file__).with_name('studio.html'))

@app.post('/studio/session')
def studio_session(response: Response, x_api_key: str | None = Header(default=None)):
    auth(x_api_key)
    response.set_cookie('clip_studio_key', settings.clip_api_key, httponly=True, samesite='strict')
    return {'ok': True}


@app.get('/studio/session')
def local_studio_session(request: Request, response: Response):
    loopback = {'localhost', '127.0.0.1', '::1'}
    if (not request.client or request.client.host not in {'127.0.0.1', '::1'}
            or request.url.hostname not in loopback):
        raise HTTPException(403, 'automatic Studio access is local only')
    response.set_cookie('clip_studio_key', settings.clip_api_key, httponly=True, samesite='strict')
    return {'ok': True}


@app.post('/studio/logout')
def studio_logout(response: Response):
    response.delete_cookie('clip_studio_key', samesite='strict')
    return {'ok': True}

@app.get('/jobs/{job_id}/media')
def job_media(job_id: str, clip_studio_key: str | None = Cookie(default=None)):
    if clip_studio_key != settings.clip_api_key:
        raise HTTPException(401, 'studio session required')
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job:
            raise HTTPException(404, 'job not found')
        source = db.get(Source, job.source_id)
    path, probe = prepare_source(source)
    if probe['codec_name'] == 'av1':
        preview = settings.clip_storage_root / 'sources' / source.id / 'studio-preview.mp4'
        if not preview.is_file() or not preview.stat().st_size or preview.stat().st_mtime_ns < path.stat().st_mtime_ns:
            temporary = preview.with_name('studio-preview.tmp.mp4')
            run(['ffmpeg', '-y', '-i', str(path), '-map', '0:v:0', '-map', '0:a?',
                 '-vf', 'scale=960:-2', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '27',
                 '-c:a', 'aac', '-b:a', '96k', '-movflags', '+faststart', str(temporary)])
            probe_video(temporary)
            temporary.replace(preview)
        path = preview
    return FileResponse(path, media_type='video/mp4')

@app.get('/health')
def health(): return {'status': 'ok'}

@app.get('/ready')
def ready():
    try:
        init_db()
        capabilities = preflight()
        try:
            acceleration = resolve_acceleration(capabilities)
        except AcceleratorError as exc:
            return {'status': 'degraded', 'accelerators': capabilities, 'error': str(exc)}
        return {'status': 'ready', 'accelerators': capabilities, 'selected': acceleration}
    except Exception as exc:
        raise HTTPException(503, str(exc)) from exc

@app.post('/sources', dependencies=[Depends(auth)])
def sources(payload: SourceCreate):
    obj = create_source(payload.uri, payload.kind)
    return {'id': obj.id, 'uri': obj.uri, 'kind': obj.kind, 'status': obj.status}

@app.post('/sources/upload', dependencies=[Depends(auth)])
async def upload(file: Annotated[UploadFile, File()]):
    try:
        base = upload_target(file.filename or '')
    except MediaError as exc:
        raise HTTPException(415, str(exc)) from exc
    target = base.with_name(f'{base.stem}-{uuid4().hex[:12]}.mp4')
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        require_free_space(target.parent)
    except MediaError as exc:
        raise HTTPException(507, str(exc)) from exc
    limit = settings.clip_max_upload_mb * 1024 * 1024
    size = 0
    try:
        with target.open('xb') as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, 'upload exceeds configured size limit')
                output.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    try:
        probe_video(target)
    except MediaError as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(422, str(exc)) from exc
    obj = create_source(str(target), 'local')
    return {'id': obj.id, 'uri': str(target), 'kind': 'local', 'status': obj.status}

@app.post('/jobs', dependencies=[Depends(auth)])
def jobs(payload: JobCreate):
    config = {'count': payload.count,
              'language': payload.language or settings.clip_language,
              'template': payload.template,
              'campaign_rules': payload.campaign_rules}
    if payload.intervals is not None:
        config['intervals'] = [interval.model_dump() for interval in payload.intervals]
    try: obj = create_job(payload.source_id, config)
    except ValueError as e: raise HTTPException(404, str(e))
    try: run_job.delay(obj.id)
    except Exception as exc: raise HTTPException(503, f'queue unavailable: {exc}') from exc
    return {'id': obj.id, 'source_id': obj.source_id, 'status': obj.status, 'attempts': obj.attempts}

@app.get('/jobs/{job_id}', dependencies=[Depends(auth)])
def get_job(job_id: str):
    result = job_view(job_id)
    if not result: raise HTTPException(404, 'job not found')
    return result

@app.get('/jobs', dependencies=[Depends(auth)])
def list_jobs():
    with SessionLocal() as db:
        rows = db.scalars(select(Job).order_by(Job.created_at.desc()).limit(50)).all()
        return [{'id': row.id, 'status': row.status, 'created_at': row.created_at.isoformat()}
                for row in rows]

@app.get('/clips/{clip_id}', dependencies=[Depends(auth)])
def get_clip(clip_id: str):
    with SessionLocal() as db: obj = db.get(Clip, clip_id)
    if not obj: raise HTTPException(404, 'clip not found')
    return clip_view(obj)

@app.get('/clips/{clip_id}/artifacts/{kind}')
def clip_artifact(clip_id: str, kind: str, clip_studio_key: str | None = Cookie(default=None)):
    if clip_studio_key != settings.clip_api_key:
        raise HTTPException(401, 'studio session required')
    if kind not in {'mp4', 'thumbnail', 'srt', 'vtt'}:
        raise HTTPException(404, 'artifact not found')
    with SessionLocal() as db:
        clip = db.get(Clip, clip_id)
        path = (clip.artifacts or {}).get(kind) if clip else None
    if not path or not Path(path).is_file():
        raise HTTPException(404, 'artifact not found')
    return FileResponse(path)

@app.put('/clips/{clip_id}/selection', dependencies=[Depends(auth)])
def put_selection(clip_id: str, payload: SelectionUpdate):
    try:
        return choose_clip(clip_id, payload.selection)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

@app.get('/clips/{clip_id}/decisions', dependencies=[Depends(auth)])
def get_decisions(clip_id: str):
    return decisions(clip_id)

@app.post('/clips/{clip_id}/review', dependencies=[Depends(auth)])
def post_review(clip_id: str, payload: ReviewAction):
    try:
        return review_clip(clip_id, payload.action, payload.reason, payload.start, payload.end,
                           payload.camera_side, payload.crop_points, payload.text_method)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

@app.post('/jobs/{job_id}/intervals', dependencies=[Depends(auth)])
def post_interval(job_id: str, payload: ManualInterval):
    try:
        return add_interval(job_id, payload.start, payload.end)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

@app.post('/clips/{clip_id}/working-copy', dependencies=[Depends(auth)])
def post_working_copy(clip_id: str):
    try:
        return prepare_working_copy(clip_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

@app.post('/clips/{clip_id}/export', dependencies=[Depends(auth)])
def post_export(clip_id: str):
    try:
        return export_clip(clip_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.patch('/clips/{clip_id}/title', dependencies=[Depends(auth)])
def patch_title(clip_id: str, payload: TitleUpdate):
    try:
        return rename_clip(clip_id, payload.title)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

@app.post('/clips/{clip_id}/finalize', dependencies=[Depends(auth)])
def post_finalize(clip_id: str, payload: FinalizeRequest):
    try:
        return finalize_cutwright(clip_id, payload.path)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

@app.post('/campaigns', dependencies=[Depends(auth)])
def campaigns(payload: CampaignCreate): return create_campaign(payload.name, payload.rules)


@app.post('/voiceovers', dependencies=[Depends(auth)])
def create_voiceover(payload: VoiceoverCreate, background_tasks: BackgroundTasks):
    try:
        result = voiceover.create(payload.clip_id, payload.path, payload.mode,
                                  render_id=payload.render_id, prompt=payload.prompt,
                                  regions=[region.model_dump() for region in payload.regions],
                                  manual=payload.manual)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if not payload.manual:
        dispatch_voiceover(voiceover_draft, voiceover.generate_draft, result['id'], background_tasks)
    return result


@app.get('/studio/projects/{project_id}/voiceovers', dependencies=[Depends(auth)])
def project_voiceovers(project_id: str):
    return voiceover.list_for_project(project_id)


@app.get('/clips/{clip_id}/voiceovers', dependencies=[Depends(auth)])
def clip_voiceovers(clip_id: str):
    return voiceover.list_for_clip(clip_id)


@app.get('/voiceovers/{voiceover_id}', dependencies=[Depends(auth)])
def get_voiceover(voiceover_id: str):
    result = voiceover.get(voiceover_id)
    if not result:
        raise HTTPException(404, 'voiceover not found')
    return result


@app.put('/voiceovers/{voiceover_id}/lines', dependencies=[Depends(auth)])
def edit_voiceover(voiceover_id: str, payload: VoiceoverEdit):
    try:
        return voiceover.edit(voiceover_id, [line.model_dump() for line in payload.lines])
    except KeyError as exc:
        raise HTTPException(404, 'voiceover not found') from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post('/voiceovers/{voiceover_id}/approve', dependencies=[Depends(auth)])
def approve_voiceover(voiceover_id: str, background_tasks: BackgroundTasks):
    try:
        result = voiceover.approve(voiceover_id)
    except KeyError as exc:
        raise HTTPException(404, 'voiceover not found') from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    dispatch_voiceover(voiceover_render, voiceover.render, voiceover_id, background_tasks)
    return result


@app.post('/voiceovers/{voiceover_id}/retry', dependencies=[Depends(auth)])
def retry_voiceover(voiceover_id: str, background_tasks: BackgroundTasks):
    with SessionLocal() as db:
        item = db.get(Voiceover, voiceover_id)
        if not item:
            raise HTTPException(404, 'voiceover not found')
        if item.status != 'failed':
            raise HTTPException(409, 'only failed voiceovers can be retried')
        item.status = 'draft' if item.lines else 'queued'
        item.error = None
        db.commit()
        result = voiceover.view(item)
    if result['status'] == 'queued':
        dispatch_voiceover(voiceover_draft, voiceover.generate_draft, voiceover_id, background_tasks)
    return result


@app.get('/voiceovers/{voiceover_id}/artifacts/{kind}')
def voiceover_artifact(voiceover_id: str, kind: str, x_api_key: str | None = Header(default=None),
                       clip_studio_key: str | None = Cookie(default=None)):
    if x_api_key != settings.clip_api_key and clip_studio_key != settings.clip_api_key:
        raise HTTPException(401, 'studio session or api key required')
    if kind not in {'wav', 'mp4'}:
        raise HTTPException(404, 'artifact not found')
    with SessionLocal() as db:
        item = db.get(Voiceover, voiceover_id)
        path = (item.artifacts or {}).get(kind) if item else None
    if not path or not Path(path).is_file():
        raise HTTPException(404, 'artifact not found')
    return FileResponse(path, media_type='audio/wav' if kind == 'wav' else 'video/mp4',
                        filename=Path(path).name, content_disposition_type='inline')

@app.post('/exports/{export_id}/retry', dependencies=[Depends(auth)])
def retry(export_id: str):
    if not job_view(export_id): raise HTTPException(404, 'export/job not found')
    with SessionLocal() as db:
        job = db.get(Job, export_id)
        if job.status not in {'retrying', 'dead_letter', 'queued', 'needs_configuration'}:
            raise HTTPException(409, 'only failed or queued jobs can be retried')
        job.status = 'queued'; job.error = None; db.commit()
    try: run_job.delay(export_id)
    except Exception as exc: raise HTTPException(503, f'queue unavailable: {exc}') from exc
    return {'id': export_id, 'status': 'queued'}

@app.get('/queue', dependencies=[Depends(auth)])
def queue():
    with SessionLocal() as db:
        return {'queued': [j.id for j in db.query(Job).filter(Job.status.in_(['queued', 'retrying'])).all()]}


app.mount('/studio', StaticFiles(directory=studio_web, html=True, check_dir=False), name='studio-web')
