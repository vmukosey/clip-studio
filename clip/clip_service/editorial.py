import json
import re
from pathlib import Path
from urllib.request import Request, urlopen

from .config import settings


PROMPT = """You are a strict editor selecting self-contained Russian interview/podcast clips.
Return JSON only: {"approved": boolean, "score": integer 0-10, "title": short moment title in the language spoken, "hook": string,
"completion": string, "intrusions": string, "boundaries": string, "reason": string}.
Approve only if the first sentence works without prior context, the thought reaches a
conclusion, there are no requests to like/subscribe or chat diversions, and boundaries
do not cut words or thoughts. Reject uncertainty. A score of 8 or more is required.
Do not treat speech recognition confidence as editorial quality."""


def transcript_text(segments: list[dict], start: float, end: float) -> str:
    return ' '.join(str(s.get('text', '')).strip() for s in segments
                    if float(s['end']) > start and float(s['start']) < end).strip()


def shortlist(segments: list[dict], duration: float, count: int,
              min_seconds: float, max_seconds: float) -> list[dict]:
    if not segments or count <= 0:
        return []
    windows = []
    for left, first in enumerate(segments):
        start = float(first['start'])
        if start < 0 or start >= duration:
            continue
        for right in range(left, len(segments)):
            end = float(segments[right]['end'])
            length = end - start
            if length > max_seconds:
                break
            if end > duration or length < min_seconds:
                continue
            text = transcript_text(segments, start, end)
            words = re.findall(r'\w+', text)
            if len(words) < max(12, int(length * .6)):
                continue
            next_gap = (float(segments[right + 1]['start']) - end
                        if right + 1 < len(segments) else 1)
            rank = (int(bool(re.search(r'[.!?…]$', text))) +
                    min(1, max(0, next_gap)) + min(1, len(words) / 55))
            windows.append((rank, start, end, text))
    chosen = []
    for _, start, end, body in sorted(windows, reverse=True):
        tokens = set(re.findall(r'\w+', body.lower()))
        if any(min(end, old['end']) > max(start, old['start']) or
               len(tokens & old['_tokens']) / max(1, len(tokens | old['_tokens'])) > .7
               for old in chosen):
            continue
        chosen.append({'start': round(start, 3), 'end': round(end, 3),
                       'text': body, '_tokens': tokens})
        if len(chosen) >= count * 4:
            break
    for item in chosen:
        del item['_tokens']
    return chosen


def judge(text: str) -> dict:
    if not settings.clip_llm_base_url or not settings.clip_llm_api_key:
        raise RuntimeError('CLIP_LLM_BASE_URL and CLIP_LLM_API_KEY are required for automatic selection')
    url = settings.clip_llm_base_url.rstrip('/') + '/chat/completions'
    data = json.dumps({'model': settings.clip_llm_model, 'temperature': 0,
                       'response_format': {'type': 'json_object'},
                       'messages': [{'role': 'system', 'content': PROMPT},
                                    {'role': 'user', 'content': text}]}).encode()
    request = Request(url, data=data, headers={'Authorization': f'Bearer {settings.clip_llm_api_key}',
                                                  'Content-Type': 'application/json'})
    with urlopen(request, timeout=90) as response:
        payload = json.load(response)
    result = json.loads(payload['choices'][0]['message']['content'])
    score = max(0, min(10, int(result.get('score', 0))))
    result['score'] = score
    result['approved'] = result.get('approved') is True and score >= 8
    return result


def picture_visible(path: Path, start: float, end: float) -> bool:
    try:
        import cv2
    except ImportError:
        return True
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        return False
    visible = False
    try:
        for fraction in (.15, .5, .85):
            capture.set(cv2.CAP_PROP_POS_MSEC, (start + (end-start) * fraction) * 1000)
            ok, frame = capture.read()
            if ok and frame is not None:
                mean, deviation = cv2.meanStdDev(frame)
                if float(mean.mean()) > 12 or float(deviation.mean()) > 10:
                    visible = True
                    break
    finally:
        capture.release()
    return visible


def recommend(segments: list[dict], duration: float, count: int,
              min_seconds: float, max_seconds: float,
              source_path: Path | None = None) -> list[dict]:
    result = []
    for item in shortlist(segments, duration, count, min_seconds, max_seconds):
        text = item['text']
        if re.search(r'ставьте лайк|подписывайтесь|подпишитесь|like and subscribe', text, re.I):
            continue
        if source_path is not None and not picture_visible(source_path, item['start'], item['end']):
            continue
        verdict = judge(text)
        if verdict['approved']:
            result.append({**item, 'score': verdict['score'] / 10,
                           'confidence': 0, 'algorithm': 'editorial-llm-v1',
                           'reasons': verdict})
        if len(result) >= count:
            break
    return sorted(result, key=lambda item: item['start'])
