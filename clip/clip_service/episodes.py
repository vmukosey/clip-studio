"""Source ordered episode discovery for automatic jobs."""
import base64
import json
from pathlib import Path
from urllib.request import Request, urlopen

from .config import settings
from .core import canonical_json, stable_id, write_atomic
from .gaming import _samples

VERSION = 'storyboard-v1'


def _box(value):
    if value is None:
        return None
    if not isinstance(value, dict) or any(not isinstance(value.get(k), (int, float))
                                          for k in ('x', 'y', 'w', 'h')):
        raise ValueError('model returned an invalid box')
    if any(not 0 <= value[k] <= 1 for k in ('x', 'y', 'w', 'h')) or value['w'] <= 0 or value['h'] <= 0:
        raise ValueError('model box is outside the image')
    if value['x'] + value['w'] > 1.001 or value['y'] + value['h'] > 1.001:
        raise ValueError('model box extends outside the image')
    return {k: round(float(value[k]), 4) for k in ('x', 'y', 'w', 'h')}


def _ask(prompt, frames):
    if not settings.clip_perlapenna_base_url or not settings.clip_perlapenna_api_key:
        raise RuntimeError('Automatic storyboard requires CLIP_PERLAPENNA_BASE_URL and CLIP_PERLAPENNA_API_KEY')
    import cv2
    content = [{'type': 'text', 'text': prompt}]
    for frame in frames:
        ok, encoded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 72])
        if ok:
            content.append({'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' +
                            base64.b64encode(encoded.tobytes()).decode('ascii')}})
    data = json.dumps({'model': settings.clip_vision_model, 'temperature': 0,
                       'response_format': {'type': 'json_object'},
                       'messages': [{'role': 'user', 'content': content}]}).encode()
    request = Request(settings.clip_perlapenna_base_url.rstrip('/') + '/chat/completions', data=data,
                      headers={'Authorization': f'Bearer {settings.clip_perlapenna_api_key}',
                               'Content-Type': 'application/json'})
    with urlopen(request, timeout=120) as response:
        return json.loads(json.load(response)['choices'][0]['message']['content'])


def _cached(cache_dir, key, prompt, frames, validate):
    path = cache_dir / f'{key}.json'
    if path.is_file():
        return validate(json.loads(path.read_text(encoding='utf-8')))
    error = None
    previous = None
    for _ in range(3):
        correction = (f' Previous response was invalid: {error}. Correct this response: {previous}. '
                      'Every box must be null or one object with numeric x,y,w,h in [0,1].'
                      if error else '')
        result = _ask(prompt + correction, frames)
        try:
            result = validate(result)
            write_atomic(path, json.dumps(result, ensure_ascii=False).encode())
            return result
        except (ValueError, KeyError, TypeError) as exc:
            error = str(exc)
            previous = canonical_json(result)[:2000]
    raise ValueError(f'model returned invalid storyboard three times: {error}')


def _validate_window(result, start, end):
    events = result.get('events')
    if not isinstance(events, list):
        raise ValueError('events must be an array')
    checked = []
    for event in events:
        a, b = float(event['start']), float(event['end'])
        if not start <= a < b <= end + .1:
            raise ValueError('event boundaries are outside the window')
        checked.append({'start': round(a, 3), 'end': round(b, 3),
                        'title': str(event.get('title') or 'Episode')[:200],
                        'description': str(event.get('description') or '')[:1000]})
    return {'events': checked}


def _validate_plan(result, start, end):
    if result.get('suitable') not in (True, False):
        raise ValueError('suitable must be boolean')
    a, b = float(result['start']), float(result['end'])
    if not start - 1 <= a < b <= end + 1:
        raise ValueError('refined boundaries are outside the episode')
    return {'suitable': result['suitable'], 'start': max(start, a), 'end': min(end, b),
            'action': _box(result.get('action')), 'facecam': _box(result.get('facecam')),
            'embedded_text': _box(result.get('embedded_text')),
            'reason': str(result.get('reason') or '')[:1000]}


def discover(path: Path, segments: list[dict], duration: float, count: int | None,
             max_seconds: float) -> list[dict]:
    stat = path.stat()
    key = stable_id(VERSION, str(path.resolve()), str(stat.st_size), str(stat.st_mtime_ns),
                    settings.clip_vision_model, canonical_json(segments), str(max_seconds))
    cache_dir = settings.clip_storage_root / 'storyboards' / key
    final_cache = cache_dir / 'episodes.json'
    if final_cache.is_file():
        candidates = json.loads(final_cache.read_text(encoding='utf-8'))
        return candidates[:count] if count is not None else candidates
    events = []
    window_size = 40.0
    for index in range(int((duration - .001) // window_size) + 1):
        start, end = index * window_size, min(duration, (index + 1) * window_size)
        cached_window = cache_dir / f'window-{index}.json'
        window = [] if cached_window.is_file() else _samples(path, end, 1.0, start)
        if not window and not cached_window.is_file():
            raise RuntimeError(f'Could not sample storyboard window {index}')
        chosen = window[::max(1, len(window) // 8)][:8]
        speech = [{'start': s['start'], 'end': s['end'], 'text': s.get('text', '')}
                  for s in segments if float(s['end']) > start and float(s['start']) < end]
        visual_cuts = [round(s[0], 2) for s in window if s[1] > .45]
        speech_pauses = [round(float(a['end']), 2) for a, b in zip(speech, speech[1:])
                         if float(b['start']) - float(a['end']) > .5]
        prompt = (f'Identify every self-contained story or event in chronological order between {start:.2f} and {end:.2f} seconds. '
                  'Camera changes within the same event stay together. Separate unrelated stories. '
                  'Return JSON {"events":[{"start":number,"end":number,"title":string,"description":string}]}. '
                  f'Frames at seconds {[round(s[0], 2) for s in chosen]}. Visual cut candidates: {visual_cuts}. '
                  f'Speech pauses: {speech_pauses}. Speech: {json.dumps(speech, ensure_ascii=False)}')
        result = _cached(cache_dir, f'window-{index}', prompt, [s[2] for s in chosen],
                         lambda r: _validate_window(r, start, end))
        events.extend(result['events'])
    merged = []
    for event in sorted(events, key=lambda e: e['start']):
        crosses_window = False
        if merged and event['start'] <= merged[-1]['end'] + .5:
            boundary = round(merged[-1]['end'] / window_size) * window_size
            if abs(merged[-1]['end'] - boundary) < 1 and abs(event['start'] - boundary) < 1:
                cached_continuity = cache_dir / f'continuity-{int(boundary)}.json'
                nearby = ([] if cached_continuity.is_file() else
                          _samples(path, min(duration, boundary + 4), 1.0, max(0, boundary - 4)))
                nearby = sorted(nearby, key=lambda s: abs(s[0] - boundary))[:4]
                speech = [s.get('text', '') for s in segments
                          if float(s['end']) > boundary - 6 and float(s['start']) < boundary + 6]
                prompt = (f'Are these the SAME uninterrupted story across {boundary:.2f}s? '
                          'Camera changes or a continuing interview answer do not create a new event. '
                          'Return JSON {"same_event":boolean}. '
                          f'Before: {merged[-1]["title"]}; after: {event["title"]}. '
                          f'Speech: {json.dumps(speech, ensure_ascii=False)}')
                verdict = _cached(cache_dir, f'continuity-{int(boundary)}', prompt,
                                  [s[2] for s in nearby],
                                  lambda r: r if isinstance(r.get('same_event'), bool)
                                  else (_ for _ in ()).throw(ValueError('same_event must be boolean')))
                crosses_window = verdict['same_event']
        if merged and (event['start'] < merged[-1]['end'] or crosses_window):
            merged[-1]['end'] = max(merged[-1]['end'], event['end'])
        else:
            merged.append(event.copy())
    candidates = []
    for index, event in enumerate(merged):
        start, end = event['start'], event['end']
        if end - start < 6 or end - start > max_seconds:
            continue
        plans = []
        event_samples = ([] if all((cache_dir / f'event-{index}-part-{part}.json').is_file()
                                   for part in range(3)) else _samples(path, end, 1.0, start))
        review_error = None
        for part, fraction in enumerate((.1, .5, .9)):
            point = start + (end - start) * fraction
            nearby = sorted(event_samples, key=lambda s: abs(s[0] - point))[:3]
            speech = [s.get('text', '') for s in segments if float(s['end']) > point - 4 and float(s['start']) < point + 4]
            prompt = (f'Check the {part + 1}/3 portion of this event ({start:.2f}-{end:.2f}s). '
                      'Return JSON {"suitable":boolean,"start":number,"end":number,"action":box|null,"facecam":box|null,'
                      '"embedded_text":box|null,"reason":string}. Boxes are normalized x,y,w,h within 0-1. '
                      'Keep speech and event boundaries intact. Prefer a complete event. '
                      f'Frames near {point:.2f}s. Speech: {json.dumps(speech, ensure_ascii=False)}')
            try:
                result = _cached(cache_dir, f'event-{index}-part-{part}', prompt, [s[2] for s in nearby],
                                 lambda r: _validate_plan(r, start, end))
            except (ValueError, RuntimeError) as exc:
                review_error = str(exc)[:1000]
                break
            plans.append({'at': round(point, 3), **result})
        if review_error:
            candidates.append({'start': start, 'end': end, 'score': 0, 'confidence': 0,
                               'algorithm': VERSION, 'title': event['title'],
                               'reasons': {'reason': event['description']}, 'plans': plans,
                               'layout': {'present': False, 'plans': plans},
                               'review_error': review_error, 'needs_fix': True})
            continue
        if not all(p['suitable'] for p in plans):
            continue
        a, b = max(start, min(p['start'] for p in plans)), min(end, max(p['end'] for p in plans))
        if b - a < 6:
            continue
        candidates.append({'start': round(a, 3), 'end': round(b, 3), 'score': 1.0,
                           'confidence': 1.0, 'algorithm': VERSION, 'title': event['title'],
                           'reasons': {'reason': event['description']}, 'plans': plans,
                           'layout': {'present': any(p['facecam'] for p in plans), 'plans': plans}})
    write_atomic(final_cache, json.dumps(candidates, ensure_ascii=False).encode())
    return candidates[:count] if count is not None else candidates
