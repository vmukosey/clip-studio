import json
import shutil
from pathlib import Path
from uuid import uuid4

try:
    import fcntl
except ImportError:  # Windows workers do not provide fcntl.
    fcntl = None
    import msvcrt

from sqlalchemy import select

from .composition import RENDER_VERSION, crop_filter, render_vertical
from .config import settings
from .core import canonical_json, stable_id, subtitles, write_atomic
from .db import Campaign, Clip, Job, ReviewDecision, SessionLocal, Source
from .export_names import available_base, export_dir, safe_name
from .editorial import recommend, transcript_text
from .gpu import resolve_acceleration
from .media import prepare_source, probe_video, require_free_space, run
from .selection import clip_segments, manual_candidates
from .transcription import transcribe, transcribe_clip
from .gaming import detect_camera, gaming_candidates, vision_review
from .episodes import discover


def create_source(uri: str, kind: str = 'local'):
    sid = stable_id(kind, uri)
    with SessionLocal() as db:
        obj = db.get(Source, sid)
        if not obj:
            obj = Source(id=sid, kind=kind, uri=uri)
            db.add(obj)
            db.commit()
        return obj


def create_job(source_id: str, config: dict | None = None):
    cfg = config or {}
    jid = uuid4().hex
    with SessionLocal() as db:
        if not db.get(Source, source_id):
            raise ValueError('source not found')
        obj = Job(id=jid, source_id=source_id, config=cfg)
        db.add(obj)
        db.commit()
        return obj


def clip_view(c):
    return {'id': c.id, 'job_id': c.job_id, 'start': c.start, 'end': c.end,
            'score': c.score, 'confidence': c.confidence, 'status': c.status,
            'selection': c.selection or 'unselected',
            'title': c.title or '',
            'artifacts': c.artifacts or {}, 'review': c.review or {}}


def job_view(job_id: str):
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job:
            return None
        clips = db.scalars(select(Clip).where(Clip.job_id == job_id).order_by(Clip.start)).all()
        source = db.get(Source, job.source_id)
        return {'id': job.id, 'source_id': job.source_id, 'status': job.status,
                'attempts': job.attempts, 'error': job.error,
                'template': job.config.get('template', 'default'),
                'source_uri': source.uri if source else '',
                'clips': [clip_view(c) for c in clips]}


def _status(db, job: Job, value: str):
    job.status = value
    db.commit()


def _review_status(db, job: Job):
    clips = db.scalars(select(Clip).where(Clip.job_id == job.id)).all()
    if job.config.get('intervals') is None:
        job.status = ('exported' if clips and all(c.status == 'exported' for c in clips)
                      else 'needs_fix' if any(c.status == 'needs_fix' for c in clips)
                      else 'rendering')
        return
    if clips and all(c.status in {'exported', 'rejected'} for c in clips) and any(
            c.status == 'exported' for c in clips):
        job.status = 'exported'
    else:
        job.status = 'awaiting_review' if clips else 'no_candidates'


def process_job(job_id: str):
    lock_path = settings.clip_storage_root / 'locks' / f'{job_id}.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a+b') as lock:
        if fcntl:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            return _process_job(job_id)
        lock.seek(0)
        lock.write(b'0')
        lock.flush()
        msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        try:
            return _process_job(job_id)
        finally:
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            except PermissionError:
                pass


def _process_job(job_id: str):
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job:
            raise ValueError('job not found')
        if job.status in {'awaiting_review', 'no_candidates', 'exported'}:
            return job_view(job_id)
        job.attempts += 1
        job.error = None
        if job.config.get('intervals') is None and not (
                settings.clip_perlapenna_base_url and settings.clip_perlapenna_api_key):
            job.status = 'needs_configuration'
            job.error = 'Set CLIP_PERLAPENNA_BASE_URL and CLIP_PERLAPENNA_API_KEY for automatic storyboard analysis'
            db.commit()
            return job_view(job_id)
        _status(db, job, 'downloading')
        source = db.get(Source, job.source_id)
        source_prepared = False
        try:
            acceleration = resolve_acceleration()
            source_path, probe = prepare_source(source)
            source_prepared = True
            source.status = 'ready'
            db.commit()

            language = str(job.config.get('language', settings.clip_language))
            intervals = job.config.get('intervals')
            stat = source_path.stat()
            transcript_key = stable_id(language, settings.whisper_model, acceleration['whisper_device'],
                                       acceleration['whisper_compute_type'], str(stat.st_size), str(stat.st_mtime_ns))
            transcript_path = settings.clip_storage_root / 'sources' / source.id / f'transcript-{transcript_key}.json'
            if intervals is not None:
                segments = []
            else:
                _status(db, job, 'transcribing')
                segments = transcribe(source_path, language, transcript_path, acceleration)

            _status(db, job, 'detecting')
            if intervals is not None:
                candidates = manual_candidates(intervals, probe['duration'],
                                               settings.clip_min_seconds, settings.clip_max_seconds)
            else:
                requested = job.config.get('count')
                candidates = discover(source_path, segments, probe['duration'], requested,
                                      settings.clip_max_seconds)
            if not candidates:
                job.error = ('No gaming intervals passed PerlaPenna review' if
                             job.config.get('template') == 'gaming' else 'No intervals passed editorial review')
                _status(db, job, 'no_candidates')
                return job_view(job_id)

            for index, candidate in enumerate(candidates):
                cid = stable_id(job.id, str(index), canonical_json({'start': candidate['start'], 'end': candidate['end']}))
                clip = db.get(Clip, cid)
                if not clip:
                    title = (candidate.get('reasons') or {}).get('title') or candidate.get('title')
                    clip = Clip(id=cid, job_id=job.id, start=candidate['start'], end=candidate['end'],
                                score=candidate['score'], confidence=candidate['confidence'],
                                status='needs_fix' if candidate.get('needs_fix') else 'candidate',
                                title=str(title).strip() if title else f'Clip {index + 1:02d}',
                                artifacts={}, review={})
                    db.add(clip)
                    db.flush()
                clip.review = {'algorithm': candidate['algorithm'],
                               'reasons': candidate.get('reasons', {'reason': 'manual interval'}),
                               'text': candidate.get('text') or transcript_text(segments, clip.start, clip.end),
                               'source_file': str(source_path), 'probe': probe,
                               'low_resolution': min(probe['width'] / (9 / 16), probe['height']) < 1920,
                               'layout': candidate.get('layout', {'present': False}),
                               'crop_points': candidate.get('crop_points', []),
                               'plans': candidate.get('plans', []),
                               'qc_error': candidate.get('review_error'),
                               'signals': candidate.get('signals', {})}
            _status(db, job, 'awaiting_review' if intervals is not None else 'rendering')
        except Exception as exc:
            db.rollback()
            job = db.get(Job, job_id)
            source = db.get(Source, job.source_id)
            if not source_prepared:
                source.status = 'failed'
            job.error = f'{type(exc).__name__}: {exc}'[:4000]
            job.status = 'dead_letter' if job.attempts >= settings.clip_max_attempts else 'retrying'
            db.commit()
            raise
    if intervals is None:
        with SessionLocal() as db:
            clip_ids = db.scalars(select(Clip.id).where(Clip.job_id == job_id).order_by(Clip.start)).all()
        for clip_id in clip_ids:
            with SessionLocal() as db:
                clip = db.get(Clip, clip_id)
                if clip.status == 'exported' or (clip.status == 'needs_fix' and not clip.artifacts):
                    continue
                clip.status = 'approved'
                db.commit()
            try:
                export_clip(clip_id)
            except Exception as exc:
                with SessionLocal() as db:
                    clip = db.get(Clip, clip_id)
                    clip.status = 'needs_fix'
                    clip.review = {**(clip.review or {}), 'qc_error': str(exc)[:1000]}
                    db.commit()
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            _review_status(db, job)
            db.commit()
    return job_view(job_id)


def decisions(clip_id: str):
    with SessionLocal() as db:
        rows = db.scalars(select(ReviewDecision).where(ReviewDecision.clip_id == clip_id)
                          .order_by(ReviewDecision.created_at)).all()
        return [{'action': row.action, 'reason': row.reason, 'start': row.start,
                 'end': row.end, 'at': row.created_at.isoformat()} for row in rows]


def choose_clip(clip_id: str, choice: str):
    if choice not in {'unselected', 'keep', 'skip'}:
        raise ValueError('selection must be unselected, keep, or skip')
    with SessionLocal() as db:
        clip = db.get(Clip, clip_id)
        if not clip:
            raise ValueError('clip not found')
        if choice == 'keep' and clip.status != 'exported':
            raise ValueError('only a ready clip can be kept')
        clip.selection = choice
        db.add(ReviewDecision(id=uuid4().hex, clip_id=clip.id, action=choice,
                              reason='', start=clip.start, end=clip.end))
        manifest = (clip.artifacts or {}).get('manifest')
        if manifest and Path(manifest).is_file():
            payload = json.loads(Path(manifest).read_text(encoding='utf-8'))
            payload['selection'] = choice
            write_atomic(Path(manifest), json.dumps(payload, ensure_ascii=False, indent=2).encode())
        db.commit()
        return clip_view(clip)


def review_clip(clip_id: str, action: str, reason: str = '',
                start: float | None = None, end: float | None = None,
                camera_side: str | None = None, crop_points: list[dict] | None = None,
                text_method: str | None = None):
    if action not in {'approve', 'reject', 'edit'}:
        raise ValueError('invalid review action')
    with SessionLocal() as db:
        clip = db.get(Clip, clip_id)
        if not clip:
            raise ValueError('clip not found')
        job = db.get(Job, clip.job_id)
        if clip.status == 'exported' and job.config.get('intervals') is not None:
            raise ValueError('exported clip cannot be changed')
        probe = (clip.review or {}).get('probe') or {}
        start = clip.start if start is None else start
        end = clip.end if end is None else end
        if not 0 <= start < end <= probe.get('duration', 0):
            raise ValueError('interval is outside source')
        length = end - start
        manual = job.config.get('intervals') is not None or (clip.review or {}).get('algorithm') == 'manual-v1'
        minimum = settings.clip_min_seconds if manual else 6
        if length > settings.clip_max_seconds or (length < minimum and probe['duration'] >= minimum):
            raise ValueError('interval duration is invalid')
        if action == 'reject' and not reason.strip():
            raise ValueError('rejection reason is required')
        if action == 'approve' and clip.status != 'approved' and manual:
            approved = db.scalars(select(Clip).where(Clip.job_id == job.id,
                                                    Clip.status.in_(['approved', 'rendering', 'exported']))).all()
            limit = int(job.config.get('count') or settings.clip_count)
            if len(approved) >= limit:
                raise ValueError('approved clip limit reached')
        clip.start, clip.end = start, end
        if camera_side is not None:
            if camera_side not in {'left', 'right', 'none'}:
                raise ValueError('camera_side must be left, right, or none')
            layout = dict((clip.review or {}).get('layout') or {})
            layout.update({'present': camera_side != 'none', 'side': None if camera_side == 'none' else camera_side})
            clip.review = {**(clip.review or {}), 'layout': layout}
        if crop_points is not None:
            if not isinstance(crop_points, list) or not all(isinstance(p, dict) and
                    all(isinstance(p.get(k), (int, float)) and 0 <= p[k] <= 1 for k in ('x', 'y'))
                    for p in crop_points):
                raise ValueError('crop points must have normalized x and y')
            clip.review = {**(clip.review or {}), 'crop_points': crop_points}
        if text_method is not None:
            if text_method not in {'auto', 'crop', 'mask', 'delogo', 'none'}:
                raise ValueError('invalid text removal method')
            layout = dict((clip.review or {}).get('layout') or {})
            layout['text_method'] = text_method
            clip.review = {**(clip.review or {}), 'layout': layout}
        clip.status = ({'approve': 'approved', 'reject': 'rejected', 'edit': 'candidate'}[action]
                       if manual else
                       {'approve': 'needs_fix', 'reject': 'needs_fix', 'edit': 'needs_fix'}[action])
        db.add(ReviewDecision(id=uuid4().hex, clip_id=clip.id, action=action,
                              reason=reason.strip(), start=start, end=end))
        _review_status(db, job)
        db.commit()
        return clip_view(clip)


def add_interval(job_id: str, start: float, end: float):
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job or job.status not in {'awaiting_review', 'no_candidates'}:
            raise ValueError('job is not ready for manual review')
        source = db.get(Source, job.source_id)
        path, probe = prepare_source(source)
        item = manual_candidates([{'start': start, 'end': end}], probe['duration'],
                                 settings.clip_min_seconds, settings.clip_max_seconds)[0]
        cid = uuid4().hex
        layout = detect_camera(path, item['start'], item['end']) if job.config.get('template') == 'gaming' else {'present': False}
        count = len(db.scalars(select(Clip).where(Clip.job_id == job_id)).all()) + 1
        clip = Clip(id=cid, job_id=job_id, start=item['start'], end=item['end'], score=0,
                    confidence=0, status='candidate', title=f'Clip {count:02d}', artifacts={},
                    review={'algorithm': 'manual-v1', 'reasons': {'reason': 'manually added'},
                            'text': '', 'source_file': str(path), 'probe': probe,
                            'low_resolution': min(probe['width'] / (9 / 16), probe['height']) < 1920,
                            'layout': layout})
        db.add(clip)
        db.add(ReviewDecision(id=uuid4().hex, clip_id=cid, action='add', reason='', start=start, end=end))
        job.status = 'awaiting_review'
        db.commit()
        return clip_view(clip)


def prepare_working_copy(clip_id: str):
    with SessionLocal() as db:
        clip = db.get(Clip, clip_id)
        if not clip or clip.status not in {'approved', 'exported'}:
            raise ValueError('clip must be approved')
        job = db.get(Job, clip.job_id)
        review = clip.review or {}
        source = Path(review['source_file'])
        duration = review['probe']['duration']
        start, end = max(0, clip.start - 15), min(duration, clip.end + 15)
        stat = source.stat()
        key = stable_id(str(source.resolve()), str(stat.st_size), str(stat.st_mtime_ns),
                        str(clip.start), str(clip.end))[:12]
        target = (settings.clip_storage_root / 'work' / f'{clip.id}-{key}.mp4').resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        video_args = (['-c:v', 'h264_nvenc', '-preset', 'p5', '-cq', '16', '-b:v', '0']
                      if resolve_acceleration()['video_encoder'] == 'h264_nvenc'
                      else ['-c:v', 'libx264', '-preset', 'fast', '-crf', '16'])
        def ready(path: Path, width: int, height: int) -> bool:
            if not path.is_file() or not path.stat().st_size:
                return False
            try:
                actual = probe_video(path)
            except Exception:
                return False
            return (actual['width'] == width and actual['height'] == height
                    and abs(actual['duration'] - (end-start)) < 1)

        # AV1 is valid as a source but is not consistently decodable by browser previews.
        needs_browser_copy = review['probe'].get('codec_name') == 'av1'
        if needs_browser_copy or not ready(target, review['probe']['width'], review['probe']['height']):
            temporary = target.with_name(target.stem + '.tmp.mp4')
            run(['ffmpeg', '-y', '-i', str(source), '-ss', str(start), '-t', str(end-start),
                 '-map', '0:v:0', '-map', '0:a?', *video_args,
                 '-c:a', 'aac', '-b:a', '192k', str(temporary)])
            probe_video(temporary)
            temporary.replace(target)
        vertical = target.with_name(f'{clip.id}-vertical.mp4')
        layout = review.get('layout') or {}
        if job.config.get('template') == 'gaming':
            layout = {**layout, 'gaming': True, 'crop_points': review.get('crop_points', [])}
        vertical_signature = stable_id(RENDER_VERSION, str(target.stat().st_mtime_ns),
                                       canonical_json(layout), str(clip.start), str(clip.end))
        vertical_cache = vertical.with_suffix('.render-cache.json')
        cached_signature = (json.loads(vertical_cache.read_text(encoding='utf-8')).get('signature')
                            if vertical_cache.is_file() else None)
        if not ready(vertical, 1080, 1920) or cached_signature != vertical_signature:
            crop, _ = crop_filter(target, review['probe'], clip.start-start, clip.end-start, layout)
            temporary = vertical.with_name(vertical.stem + '.tmp.mp4')
            filter_args = (['-filter_complex', f'[0:v]{crop}[vout]', '-map', '[vout]']
                           if ';' in crop else ['-vf', crop, '-map', '0:v:0'])
            run(['ffmpeg', '-y', '-i', str(target), *filter_args,
                 '-map', '0:a?', *video_args,
                 '-c:a', 'aac', '-b:a', '192k', str(temporary)])
            probe_video(temporary)
            temporary.replace(vertical)
            write_atomic(vertical_cache, json.dumps({'signature': vertical_signature}).encode('utf-8'))
        project = target.with_name(f'{clip.id}.cutwright.json')
        length = round(clip.end - clip.start, 3)
        handle = round(clip.start - start, 3)
        payload = {'version': 1, 'name': f'Clip {clip.id[:10]}', 'width': 1080,
                   'height': 1920, 'fps': 30,
                   'media': [
                       {'id': 'vertical', 'name': vertical.name, 'kind': 'video',
                        'src': str(vertical), 'duration': end-start, 'color': '#64c8c1',
                        'width': 1080, 'height': 1920, 'hasAudio': review['probe']['has_audio']},
                       {'id': 'full', 'name': target.name, 'kind': 'video',
                        'src': str(target), 'duration': end-start, 'color': '#ecb86b',
                        'width': review['probe']['width'], 'height': review['probe']['height'],
                        'hasAudio': review['probe']['has_audio']}],
                   'clips': [{'id': 'draft', 'mediaId': 'vertical', 'trackId': 'v1',
                              'name': 'Proposed cut', 'start': 0, 'duration': length,
                              'offset': handle, 'color': '#64c8c1'}],
                   'transitions': [], 'subtitles': [], 'customTransitions': [],
                   'markers': [{'id': 'in', 'time': 0, 'label': 'Proposed in', 'color': '#b6e35a'},
                               {'id': 'out', 'time': length, 'label': 'Proposed out', 'color': '#b6e35a'}]}
        write_atomic(project, json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8'))
        return {'path': str(target), 'vertical_path': str(vertical), 'project_path': str(project),
                'source_start': start, 'source_end': end,
                'timeline_start': clip.start-start, 'timeline_end': clip.end-start,
                'left_handle': clip.start-start, 'right_handle': end-clip.end}


def export_clip(clip_id: str):
    with SessionLocal() as db:
        clip = db.get(Clip, clip_id)
        if not clip or clip.status not in {'approved', 'needs_fix'}:
            raise ValueError('clip must be approved before export')
        job = db.get(Job, clip.job_id)
        source = db.get(Source, job.source_id)
        source_path, probe = prepare_source(source)
        outdir, base = _export_target(db, job, source, clip)
        mp4, srt, vtt, manifest, thumb = (outdir / f'{base}.{ext}' for ext in
                                          ('mp4', 'srt', 'vtt', 'json', 'jpg'))
        acceleration = resolve_acceleration()
        local = _export_segments(source, source_path, job, clip, acceleration)
        if job.config.get('intervals') is None:
            clip.review = {**(clip.review or {}),
                           'text': ' '.join(segment['text'].replace('\n', ' ') for segment in local)}
        write_atomic(srt, subtitles(local, 'srt').encode('utf-8'))
        write_atomic(vtt, subtitles(local, 'vtt').encode('utf-8'))
        clip.status = 'rendering'; db.commit()
        try:
            layout = (clip.review or {}).get('layout') or {}
            if job.config.get('template') == 'gaming':
                layout = {**layout, 'gaming': True,
                          'crop_points': (clip.review or {}).get('crop_points', [])}
            render_info = render_vertical(source_path, probe, clip.start, clip.end, srt, mp4, thumb,
                                          job.config.get('campaign_rules', {}), acceleration, layout,
                                          bool(clip.artifacts))
            qc = inspect_export(mp4, probe['has_audio'], clip.end-clip.start)
            needs_fix = any('black_start:' in warning or 'freeze_start:' in warning
                            for warning in qc.get('warnings', []))
            artifacts = {'mp4': str(mp4), 'thumbnail': str(thumb), 'srt': str(srt),
                         'vtt': str(vtt), 'manifest': str(manifest)}
            if not all(Path(path).is_file() for path in artifacts.values() if path != str(manifest)) or not all(
                    Path(artifacts[key]).stat().st_size > 0 for key in ('mp4', 'thumbnail', 'vtt')):
                raise ValueError('export bundle is incomplete')
            write_atomic(manifest, json.dumps({'job_id': job.id, 'clip_id': clip.id, 'title': clip.title,
                'start': clip.start, 'end': clip.end, 'review': clip.review,
                'composition': render_info, 'qc': qc, 'artifacts': artifacts,
                'status': 'needs_fix' if needs_fix else 'ready',
                'selection': clip.selection or 'unselected'}, ensure_ascii=False, indent=2).encode())
            clip.artifacts = artifacts
            clip.review = {**(clip.review or {}), 'qc': qc}
            clip.status = 'needs_fix' if needs_fix else 'exported'
            _review_status(db, job)
            db.commit()
            return clip_view(clip)
        except Exception:
            clip.status = 'needs_fix' if job.config.get('intervals') is None else 'approved'
            db.commit()
            raise


def inspect_export(path: Path, source_has_audio: bool, expected_duration: float | None = None):
    qc = probe_video(path)
    if qc['codec_name'] != 'h264' or qc['width'] != 1080 or qc['height'] != 1920:
        raise ValueError('final MP4 must be H.264 at 1080x1920')
    if expected_duration is not None and abs(qc['duration'] - expected_duration) > 1:
        raise ValueError('export duration differs from approved interval')
    if not 6 <= qc['duration'] <= settings.clip_max_seconds + 1:
        raise ValueError('final MP4 duration is outside configured limits')
    if source_has_audio and not qc['has_audio']:
        raise ValueError('export lost audio')
    if source_has_audio and qc['audio_codec_name'] != 'aac':
        raise ValueError('final MP4 audio must be AAC')
    checks = run(['ffmpeg', '-i', str(path), '-vf',
                  'blackdetect=d=0.5:pix_th=0.1,freezedetect=n=-50dB:d=2',
                  '-af', 'silencedetect=n=-50dB:d=2', '-f', 'null', '-'])
    qc['warnings'] = [line.strip() for line in checks.stderr.splitlines()
                      if any(marker in line for marker in
                             ('black_start:', 'freeze_start:', 'silence_start:'))]
    return qc


def finalize_cutwright(clip_id: str, edited_path: str):
    with SessionLocal() as db:
        clip = db.get(Clip, clip_id)
        if not clip or clip.status != 'approved':
            raise ValueError('clip must be approved before finalizing')
        source = Path(edited_path).expanduser().resolve()
        if source.suffix.lower() != '.mp4' or not source.is_file():
            raise ValueError('CutWright export must be an existing MP4')
        job = db.get(Job, clip.job_id)
        source_probe = (clip.review or {}).get('probe') or {}
        qc = inspect_export(source, bool(source_probe.get('has_audio')))
        source_record = db.get(Source, job.source_id)
        outdir, base = _export_target(db, job, source_record, clip)
        require_free_space(outdir, max(1, source.stat().st_size // (1024 * 1024)))
        mp4 = outdir / f'{base}.mp4'
        if mp4.resolve() == source:
            raise ValueError('CutWright export must be separate from service artifacts')
        temporary = mp4.with_name(mp4.stem + '.tmp.mp4')
        shutil.copy2(source, temporary)
        temporary.replace(mp4)
        manifest = outdir / f'{base}.json'
        thumb, srt, vtt = (outdir / f'{base}.{ext}' for ext in ('jpg', 'srt', 'vtt'))
        original, _ = prepare_source(source_record)
        local = _export_segments(source_record, original, job, clip, resolve_acceleration())
        write_atomic(srt, subtitles(local, 'srt').encode('utf-8'))
        write_atomic(vtt, subtitles(local, 'vtt').encode('utf-8'))
        run(['ffmpeg', '-y', '-ss', '.5', '-i', str(mp4), '-frames:v', '1', '-q:v', '2', str(thumb)])
        artifacts = {'mp4': str(mp4), 'thumbnail': str(thumb), 'srt': str(srt),
                     'vtt': str(vtt), 'manifest': str(manifest)}
        write_atomic(manifest, json.dumps({'job_id': job.id, 'clip_id': clip.id, 'title': clip.title,
                     'editor': 'CutWright', 'edited_source': str(source),
                     'review': clip.review, 'qc': qc, 'artifacts': artifacts},
                     ensure_ascii=False, indent=2).encode('utf-8'))
        clip.artifacts = artifacts
        clip.review = {**(clip.review or {}), 'qc': qc}
        clip.status = 'exported'
        _review_status(db, job)
        db.commit()
        return clip_view(clip)


def _export_target(db, job, source, clip):
    outdir = export_dir(source, job)
    existing = (clip.artifacts or {}).get('mp4')
    if existing and Path(existing).parent == outdir:
        return outdir, Path(existing).stem
    ordered = db.scalars(select(Clip).where(Clip.job_id == job.id).order_by(Clip.start, Clip.id)).all()
    number = next((i for i, item in enumerate(ordered, 1) if item.id == clip.id), 1)
    if job.config.get('intervals') is None:
        return outdir, f'{number:02d} - {safe_name(clip.title or f"Clip {number:02d}")}'
    return outdir, available_base(outdir, number, clip.title or f'Clip {number:02d}')


def _export_segments(source, source_path, job, clip, acceleration):
    stat = source_path.stat()
    language = str(job.config.get('language', settings.clip_language))
    key = stable_id(language, settings.whisper_model, acceleration['whisper_device'],
                    acceleration['whisper_compute_type'], str(stat.st_size), str(stat.st_mtime_ns))
    transcript = settings.clip_storage_root / 'sources' / source.id / f'transcript-{key}.json'
    if job.config.get('intervals') is not None:
        segments = json.loads(transcript.read_text(encoding='utf-8')).get('segments', []) if transcript.is_file() else []
        return clip_segments(segments, clip.start, clip.end)
    if not (clip.review or {}).get('probe', {}).get('has_audio'):
        return []
    clip_key = stable_id('clip-transcript-v1', str(source_path.resolve()), str(stat.st_size),
                         str(stat.st_mtime_ns), str(clip.start), str(clip.end), language,
                         settings.whisper_model, acceleration['whisper_device'],
                         acceleration['whisper_compute_type'])
    clip_cache = settings.clip_storage_root / 'sources' / source.id / 'clip-transcripts' / f'{clip_key}.json'
    local = transcribe_clip(source_path, clip.start, clip.end, language, clip_cache, acceleration)
    cues = []
    for segment in local:
        words = segment['text'].split()
        if not words:
            continue
        chunks, lines, line = [], [], ''
        for word in words:
            if line and len(line) + len(word) + 1 > 27:
                lines.append(line)
                line = word
            else:
                line = f'{line} {word}'.strip()
            if len(lines) == 2:
                chunks.append('\n'.join(lines))
                lines = []
        if line:
            lines.append(line)
        if lines:
            chunks.append('\n'.join(lines))
        span = (segment['end'] - segment['start']) / len(chunks)
        for index, text in enumerate(chunks):
            cues.append({'start': segment['start'] + index * span,
                         'end': segment['start'] + (index + 1) * span, 'text': text})
    return cues


def rename_clip(clip_id: str, title: str):
    title = title.strip()
    if not title:
        raise ValueError('title is required')
    with SessionLocal() as db:
        clip = db.get(Clip, clip_id)
        if not clip:
            raise ValueError('clip not found')
        if clip.status != 'exported':
            clip.title = title
            db.commit()
            return clip_view(clip)
        artifacts = dict(clip.artifacts or {})
        old_paths = {key: Path(value) for key, value in artifacts.items()}
        if not old_paths or any(not path.is_file() for path in old_paths.values()):
            raise ValueError('export artifacts are missing')
        outdir = old_paths['mp4'].parent
        old_base = old_paths['mp4'].stem
        match = __import__('re').match(r'^(\d+) - ', old_base)
        if match:
            number = int(match.group(1))
        else:
            job = db.get(Job, clip.job_id)
            source = db.get(Source, job.source_id)
            _, proposed = _export_target(db, job, source, clip)
            number = int(proposed.split(' - ', 1)[0])
        base = available_base(outdir, number, title, set(old_paths.values()))
        new_paths = {key: outdir / f'{base}{path.suffix}' for key, path in old_paths.items()}
        moved = []
        try:
            for key, old in old_paths.items():
                new = new_paths[key]
                if old != new:
                    old.rename(new)
                    moved.append((old, new))
            manifest = new_paths.get('manifest')
            if manifest:
                payload = json.loads(manifest.read_text(encoding='utf-8'))
                payload['title'] = title
                payload['artifacts'] = {key: str(path) for key, path in new_paths.items()}
                write_atomic(manifest, json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8'))
            clip.title = title
            clip.artifacts = {key: str(path) for key, path in new_paths.items()}
            db.commit()
        except Exception:
            db.rollback()
            for old, new in reversed(moved):
                new.rename(old)
            raise
        return clip_view(clip)


def create_campaign(name, rules):
    cid = stable_id('campaign', name, canonical_json(rules))
    with SessionLocal() as db:
        obj = db.get(Campaign, cid) or Campaign(id=cid, name=name, rules=rules)
        db.add(obj)
        db.commit()
        return {'id': cid, 'name': name, 'rules': rules}
