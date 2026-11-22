from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException

from .api import get_clip, retry
from .config import settings
from .db import Job, SessionLocal, init_db
from .service import create_campaign, create_job, create_source, job_view
from .tasks import run_job


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield

app = FastAPI(title='Clip MCP Streamable HTTP', lifespan=lifespan)

@app.get('/health')
def health(): return {'status': 'ok'}

@app.post('/mcp')
def mcp(payload: dict, x_api_key: str | None = Header(default=None)):
    if x_api_key != settings.clip_api_key: raise HTTPException(401, 'invalid api key')
    method = payload.get('name') or payload.get('method'); args = payload.get('arguments', payload.get('params', {}))
    if method == 'create_source': result = {'id': create_source(args['uri'], args.get('kind', 'local')).id}
    elif method == 'create_job':
        job = create_job(args['source_id'], args.get('config', {}))
        try: run_job.delay(job.id)
        except Exception as exc: raise HTTPException(503, f'queue unavailable: {exc}') from exc
        result = {'id': job.id}
    elif method == 'get_job': result = job_view(args['job_id'])
    elif method == 'get_clip': result = get_clip(args['clip_id'])
    elif method == 'create_campaign': result = create_campaign(args['name'], args.get('rules', {}))
    elif method == 'retry_export': result = retry(args['id'])
    elif method == 'get_queue':
        with SessionLocal() as db:
            result = {'queued': [job.id for job in db.query(Job).filter(Job.status.in_(['queued', 'retrying'])).all()]}
    else: raise HTTPException(404, 'unknown tool')
    return {'jsonrpc': '2.0', 'id': payload.get('id'), 'result': result}
