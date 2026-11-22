from datetime import datetime, timedelta, timezone

from celery import Celery
from sqlalchemy import select

from .config import settings
from .db import Job, SessionLocal, Source, Voiceover, init_db
from .service import job_view, process_job
from .voiceover import generate_draft, render

celery_app = Celery('clip', broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.task_default_queue = 'clips'
celery_app.conf.beat_schedule = {
    'clip-stale-jobs': {'task': 'clip.recover_stale', 'schedule': 600.0},
}


@celery_app.task(bind=True, name='clip.run_job', queue='clips')
def run_job(self, job_id: str):
    init_db()
    try:
        return process_job(job_id)
    except Exception as exc:  # noqa: BLE001 - every pipeline failure follows the retry policy
        current = job_view(job_id)
        if current and current['status'] == 'dead_letter':
            return current
        raise self.retry(exc=exc, countdown=min(300, 2 ** self.request.retries),
                         max_retries=max(0, settings.clip_max_attempts - 1))


@celery_app.task(name='clip.voiceover_draft', queue='clips')
def voiceover_draft(voiceover_id: str):
    init_db()
    return generate_draft(voiceover_id)


@celery_app.task(name='clip.voiceover_render', queue='clips')
def voiceover_render(voiceover_id: str):
    init_db()
    return render(voiceover_id)


@celery_app.task(name='clip.cleanup')
def cleanup_exports():
    return {'removed': []}


@celery_app.task(name='clip.recover_stale')
def recover_stale_jobs():
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=settings.clip_stale_minutes)
    active = ['downloading', 'transcribing', 'detecting', 'composing', 'rendering', 'packaging']
    with SessionLocal() as db:
        jobs = db.scalars(select(Job).where(Job.status.in_(active), Job.updated_at < cutoff)).all()
        for job in jobs:
            job.status = 'retrying'
            job.error = 'stale job recovered by scheduler'
        voiceovers = db.scalars(select(Voiceover).where(
            Voiceover.status.in_(['analyzing', 'rendering']), Voiceover.updated_at < cutoff)).all()
        for item in voiceovers:
            item.status = 'failed'
            item.error = 'stale voiceover task recovered; retry the draft or approve the saved lines again'
        db.commit()
        return {'recovered': [job.id for job in jobs],
                'voiceovers': [item.id for item in voiceovers]}
