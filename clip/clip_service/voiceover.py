"""Local, review-first narration of finished MP4 exports."""

import base64
from collections import Counter
import json
import math
import os
import re
import subprocess
import sys
import wave
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import select

from .config import settings
from .db import Clip, SessionLocal, StudioRender, Voiceover
from .media import probe_video, run
from .transcription import transcribe


MODES = {
    'description': 'Нейтральная аудиодескрипция. Описывай только видимое и говори в паузах речи.',
    'commentary': 'Короткий эмоциональный комментарий к событиям. Не выдумывай детали.',
    'intro': 'Одно короткое вступление в начале ролика. Не раскрывай неочевидные события.',
}


def view(item: Voiceover) -> dict:
    return {'id': item.id, 'clip_id': item.clip_id, 'mode': item.mode,
            'project_id': item.project_id, 'render_id': item.render_id,
            'prompt': item.prompt or '', 'regions': item.regions or [],
            'status': item.status, 'lines': item.lines or [], 'error': item.error,
            'artifacts': {key: f'/voiceovers/{item.id}/artifacts/{key}'
                          for key in (item.artifacts or {})},
            'created_at': item.created_at.isoformat()}


def validate_regions(regions: list[dict], duration: float) -> list[dict]:
    if len(regions) > 30:
        raise ValueError('too many voiceover regions')
    result = []
    for region in sorted(regions, key=lambda row: row['start']):
        start, end = round(float(region['start']), 3), round(float(region['end']), 3)
        if not 0 <= start < end <= duration or (result and start < result[-1]['end']):
            raise ValueError('voiceover regions must fit the video and not overlap')
        result.append({'start': start, 'end': end})
    return result


def lines_in_regions(lines: list[dict], regions: list[dict]):
    if regions and any(not any(line['start'] >= region['start'] and line['end'] <= region['end']
                           for region in regions) for line in lines):
        raise ValueError('voiceover lines must fit the selected regions')


def create(clip_id: str | None, path: str | None, mode: str, *, render_id: str | None = None,
           prompt: str = '', regions: list[dict] | None = None, manual: bool = False) -> dict:
    if sum(bool(value) for value in (clip_id, path, render_id)) != 1:
        raise ValueError('provide exactly one of clip_id, path or render_id')
    if mode not in MODES:
        raise ValueError('invalid voiceover mode')
    with SessionLocal() as db:
        project_id = None
        if render_id:
            render = db.get(StudioRender, render_id)
            if not render or not Path(render.path).is_file():
                raise ValueError('render not found')
            path = render.path
            project_id = render.project_id
        if clip_id:
            clip = db.get(Clip, clip_id)
            if not clip or clip.status != 'exported':
                raise ValueError('clip must have an exported MP4')
            path = (clip.artifacts or {}).get('mp4')
        source = Path(path or '').expanduser().resolve()
        if source.suffix.lower() != '.mp4' or not source.is_file():
            raise ValueError('source must be an existing MP4')
        duration = probe_video(source)['duration']
        allowed = validate_regions(regions or [], duration)
        active = db.scalars(select(Voiceover).where(
            Voiceover.source_path == str(source),
            Voiceover.status.in_(['queued', 'analyzing', 'approved', 'rendering']))).first()
        if active:
            raise ValueError(f'voiceover {active.id} is already in progress')
        item = Voiceover(id=uuid4().hex, clip_id=clip_id, source_path=str(source),
                         project_id=project_id, render_id=render_id, prompt=prompt.strip(),
                         regions=allowed, mode=mode, status='draft' if manual else 'queued',
                         lines=[], artifacts={})
        db.add(item)
        db.commit()
        return view(item)


def get(voiceover_id: str) -> dict | None:
    with SessionLocal() as db:
        item = db.get(Voiceover, voiceover_id)
        return view(item) if item else None


def list_for_clip(clip_id: str) -> list[dict]:
    with SessionLocal() as db:
        return [view(item) for item in db.scalars(select(Voiceover).where(
            Voiceover.clip_id == clip_id).order_by(Voiceover.created_at.desc())).all()]


def list_for_project(project_id: str) -> list[dict]:
    with SessionLocal() as db:
        return [view(item) for item in db.scalars(select(Voiceover).where(
            Voiceover.project_id == project_id).order_by(Voiceover.created_at.desc())).all()]


def validate_lines(lines: list[dict], duration: float) -> list[dict]:
    if not isinstance(lines, list) or len(lines) > 30:
        raise ValueError('voiceover must contain at most 30 lines')
    result = []
    for line in sorted(lines, key=lambda item: item['start']):
        start, end = round(float(line['start']), 3), round(float(line['end']), 3)
        content = str(line['text']).strip()
        if (not content or not content.strip('.…!? ') or not re.search('[А-Яа-яЁё]', content)
                or len(content) > 500
                or not 0 <= start < end <= duration or (result and start < result[-1]['end'])):
            raise ValueError('lines must contain text, fit the video, and not overlap')
        result.append({'start': start, 'end': end, 'text': content})
    if not result:
        raise ValueError('at least one line is required')
    return result


def validate_draft_quality(lines: list[dict]) -> None:
    texts = [line['text'].casefold().strip() for line in lines]
    if len(texts) != len(set(texts)):
        raise ValueError('model repeated the same line; edit the draft manually or retry')
    for line in lines:
        words = re.findall(r'[а-яё]{4,}', line['text'].casefold())
        if any(count > 5 and count > len(words) / 3 for count in Counter(words).values()):
            raise ValueError('model repeated words; edit the draft manually or retry')


def speech_gaps(segments: list[dict], duration: float, minimum: float = 1.5) -> list[dict]:
    gaps = []
    cursor = 0.0
    for segment in sorted(segments, key=lambda row: row['start']):
        start = max(0.0, float(segment['start']) - 0.15)
        end = min(duration, float(segment['end']) + 0.15)
        if start - cursor >= minimum:
            gaps.append({'start': round(cursor, 3), 'end': round(start, 3)})
        cursor = max(cursor, end)
    if duration - cursor >= minimum:
        gaps.append({'start': round(cursor, 3), 'end': round(duration, 3)})
    return gaps


def edit(voiceover_id: str, lines: list[dict]) -> dict:
    with SessionLocal() as db:
        item = db.get(Voiceover, voiceover_id)
        if not item:
            raise KeyError(voiceover_id)
        if item.status not in {'draft', 'needs_revision', 'failed', 'complete'}:
            raise ValueError('draft is currently being processed')
        item.lines = validate_lines(lines, probe_video(Path(item.source_path))['duration'])
        lines_in_regions(item.lines, item.regions or [])
        item.status = 'draft'
        item.error = None
        item.artifacts = {}
        db.commit()
        return view(item)


def approve(voiceover_id: str) -> dict:
    with SessionLocal() as db:
        item = db.get(Voiceover, voiceover_id)
        if not item:
            raise KeyError(voiceover_id)
        if item.status not in {'draft', 'needs_revision', 'failed'}:
            raise ValueError('only an editable draft can be approved')
        item.lines = validate_lines(item.lines or [], probe_video(Path(item.source_path))['duration'])
        lines_in_regions(item.lines, item.regions or [])
        if item.mode == 'description':
            transcript_path = settings.clip_storage_root / 'voiceovers' / item.id / 'transcript-auto.json'
            if transcript_path.is_file():
                segments = json.loads(transcript_path.read_text(encoding='utf-8')).get('segments', [])
                for line in item.lines:
                    if any(line['start'] < segment['end'] + 0.15 and
                           line['end'] > segment['start'] - 0.15 for segment in segments):
                        raise ValueError('audio description must fit a pause in source speech')
        item.status = 'approved'
        item.error = None
        db.commit()
        return view(item)


def _frames(source: Path, duration: float) -> list[tuple[float, str]]:
    count = min(6, max(1, math.ceil(duration / 5)))
    times = [round(i * duration / count, 2) for i in range(count)]
    result = []
    for position in times:
        frame = subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(position), '-i', str(source),
                                '-frames:v', '1', '-vf', 'scale=384:-2', '-f', 'image2pipe',
                                '-vcodec', 'mjpeg', '-'], capture_output=True, timeout=60, check=True)
        result.append((position, base64.b64encode(frame.stdout).decode('ascii')))
    return result


def _draft(source: Path, mode: str, duration: float, work: Path,
           user_prompt: str = '', regions: list[dict] | None = None) -> list[dict]:
    transcript = [{**segment, 'end': min(duration, segment['end'])}
                  for segment in transcribe(source, '', work / 'transcript-auto.json')
                  if segment['start'] < duration]
    frames = _frames(source, duration)
    speech = [{'start': segment['start'], 'end': segment['end']} for segment in transcript]
    gaps = speech_gaps(transcript, duration) if mode == 'description' else []
    if mode == 'description' and not gaps:
        raise ValueError('no pause of at least 1.5 seconds for audio description in this MP4')
    prompt = (f'{MODES[mode]} Видео длится {duration:.2f} секунд. '
              f'Интервалы исходной речи: {json.dumps(speech, ensure_ascii=False)}. '
              f'Доступные паузы для описания: {json.dumps(gaps, ensure_ascii=False)}. '
              'Верни JSON с массивом lines; в каждом элементе start, end и text. '
              'Все таймкоды в секундах относительно этого видео. Пиши конкретные короткие '
              'реплики на русском о событиях, которые действительно видны в кадрах. '
              'Одна реплика — одно законченное предложение из 5–10 слов, не более 60 символов. '
              'Не пересказывай и не имитируй исходную речь; описывай только изображение. '
              'Не давай зрителю советов и не используй первое лицо. '
              'Не используй заглушки, многоточия или общий текст без фактов. '
              'Реплики не пересекаются и заканчиваются до конца ролика. '
              'Выделяй не меньше одной секунды на каждые 10 букв текста. '
              'При аудиодескрипции избегай интервалов с речью. '
              'Для вступления верни ровно одну реплику в начале. '
              'Для комментария верни не больше трёх реплик. '
              'Для аудиодескрипции используй только доступные паузы.')
    if user_prompt:
        prompt += f' Дополнительное пожелание редактора: {user_prompt[:2000]}'
    if regions:
        prompt += (' Размещай реплики только внутри выбранных интервалов: '
                   + json.dumps(regions, ensure_ascii=False) + '.')
    schema = {'type': 'object', 'properties': {'lines': {'type': 'array',
              'minItems': 1, 'maxItems': 1 if mode == 'intro' else 3 if mode == 'commentary' else 8,
              'items': {'type': 'object', 'properties': {
                  'start': {'type': 'number', 'minimum': 0, 'maximum': duration},
                  'end': {'type': 'number', 'minimum': 0, 'maximum': duration},
                  'text': {'type': 'string', 'minLength': 2, 'maxLength': 60}},
                  'required': ['start', 'end', 'text']}}}, 'required': ['lines']}
    # Ollama's native chat API accepts images on the message, not OpenAI content blocks.
    response = httpx.post(settings.clip_ollama_url.rstrip('/') + '/api/chat', json={
        'model': settings.clip_ollama_model, 'stream': False, 'format': schema,
        'think': False, 'options': {'num_ctx': 4096 if len(frames) <= 2 else 8192,
                                   'num_predict': 400, 'temperature': 0.2},
        'messages': [{'role': 'system', 'content': 'Ты пишешь черновик русской озвучки по кадрам видео. '
                      'Текст каждой реплики должен быть только на русском языке. '
                      'Описывай конкретные видимые объекты и действия. '
                      'Текст исходной речи тебе не дан; интервалы речи нужны лишь для выбора времени.'},
                     {'role': 'user', 'content': prompt + '\nКадры: ' +
                      ', '.join(f'{t:.2f} с' for t, _ in frames),
                      'images': [image for _, image in frames]}]}, timeout=600)
    if response.is_error:
        raise RuntimeError(f'Ollama returned {response.status_code}: {response.text[:1000]}')
    payload = json.loads(response.json()['message']['content'])
    lines = validate_lines(payload['lines'], duration)
    lines_in_regions(lines, regions or [])
    validate_draft_quality(lines)
    if mode == 'intro':
        lines[0]['start'] = 0.0
        lines[0]['end'] = round(min(duration, max(4.0, len(lines[0]['text']) / 8)), 3)
        lines_in_regions(lines, regions or [])
    if mode == 'description' and any(not any(
            line['start'] >= gap['start'] and line['end'] <= gap['end'] for gap in gaps)
            for line in lines):
        raise ValueError('model placed audio description outside a source-speech pause')
    return lines


def generate_draft(voiceover_id: str) -> dict:
    with SessionLocal() as db:
        item = db.get(Voiceover, voiceover_id)
        if not item or item.status != 'queued':
            return view(item) if item else {}
        item.status = 'analyzing'; db.commit()
        try:
            source = Path(item.source_path)
            work = settings.clip_storage_root / 'voiceovers' / item.id
            work.mkdir(parents=True, exist_ok=True)
            lines = _draft(source, item.mode, probe_video(source)['duration'], work,
                           item.prompt or '', item.regions or [])
            item.lines = lines
            item.status = 'draft'
            item.error = None
        except Exception as exc:
            item.status = 'failed'
            item.error = str(exc)[:1000]
        db.commit()
        return view(item)


def _speak(text: str, target: Path):
    model = settings.clip_piper_model.resolve()
    if not model.is_file():
        raise RuntimeError(f'Piper voice model is missing: {model}')
    try:
        subprocess.run([sys.executable, '-m', 'clip_service.piper_worker', str(model), str(target)],
                       input=text, text=True, encoding='utf-8',
                       env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
                       capture_output=True, check=True, timeout=120)
    except FileNotFoundError as exc:
        raise RuntimeError('Piper executable is missing') from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f'Piper failed: {exc.stderr[-1000:]}') from exc


def render(voiceover_id: str) -> dict:
    with SessionLocal() as db:
        item = db.get(Voiceover, voiceover_id)
        if not item or item.status != 'approved':
            return view(item) if item else {}
        item.status = 'rendering'; db.commit()
        work = settings.clip_storage_root / 'voiceovers' / item.id
        work.mkdir(parents=True, exist_ok=True)
        try:
            source = Path(item.source_path)
            duration = probe_video(source)['duration']
            lines = validate_lines(item.lines, duration)
            phrases = []
            for index, line in enumerate(lines):
                target = work / f'line-{index:02d}.wav'
                _speak(line['text'], target)
                with wave.open(str(target)) as wav:
                    length = wav.getnframes() / wav.getframerate()
                if length > line['end'] - line['start'] + 0.02:
                    raise ValueError(f'Реплика {index + 1} длиннее интервала на {length - (line["end"] - line["start"]):.2f} с; сократите текст или расширьте интервал')
                phrases.append(target)
            inputs = [part for wav in phrases for part in ('-i', str(wav))]
            filters = []
            labels = []
            for index, line in enumerate(lines):
                delay = round(line['start'] * 1000)
                filters.append(f'[{index}:a]adelay={delay}|{delay}[v{index}]')
                labels.append(f'[v{index}]')
            filters.append(''.join(labels) + f'amix=inputs={len(labels)}:duration=longest:normalize=0,aresample=48000,apad,atrim=duration={duration}[voice]')
            track = work / 'voice.tmp.wav'
            run(['ffmpeg', '-y', *inputs, '-filter_complex', ';'.join(filters),
                 '-map', '[voice]', '-c:a', 'pcm_s16le', str(track)])
            video = work / 'voiced.tmp.mp4'
            has_audio = probe_video(source)['has_audio']
            voice_gain = max(0.1, min(3.0, settings.clip_voice_gain))
            mix = (f'[1:a]volume={voice_gain}[spoken];[0:a][spoken]amix=inputs=2:duration=first:normalize=0,'
                   'alimiter=limit=0.95[out]') if has_audio else f'[1:a]volume={voice_gain},alimiter=limit=0.95[out]'
            run(['ffmpeg', '-y', '-i', str(source), '-i', str(track), '-filter_complex', mix,
                 '-map', '0:v:0', '-map', '[out]', '-c:v', 'copy', '-c:a', 'aac',
                 '-b:a', '192k', '-t', str(duration), '-movflags', '+faststart', str(video)])
            if abs(probe_video(video)['duration'] - duration) > 0.1:
                raise RuntimeError('voiceover changed video duration')
            track.replace(work / 'voice.wav')
            video.replace(work / 'voiced.mp4')
            item.artifacts = {'wav': str(work / 'voice.wav'), 'mp4': str(work / 'voiced.mp4')}
            item.status = 'complete'
            item.error = None
        except ValueError as exc:
            item.status = 'needs_revision'
            item.error = str(exc)
        except Exception as exc:
            item.status = 'failed'
            item.error = str(exc)[:1000]
        db.commit()
        return view(item)
