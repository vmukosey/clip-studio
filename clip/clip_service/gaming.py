"""Gaming moment discovery and scene layout helpers."""
import base64
import json
import shutil
import statistics
import subprocess
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen

from .config import settings
from .core import stable_id, write_atomic
from .media import measure_loudness


def _samples(path: Path, duration: float, step: float = 2.0, start: float = 0.0):
    try:
        import cv2
        import numpy as np
    except ImportError:
        return []
    width, height = 320, 180
    local_ffmpeg = next((p for p in (Path(__file__).resolve().parents[1] / 'tools' / 'ffmpeg').glob('*/bin/ffmpeg.exe')), None)
    executable = shutil.which('ffmpeg') or str(local_ffmpeg or 'ffmpeg')
    command = [executable, '-v', 'error', '-ss', str(start), '-i', str(path),
               '-t', str(max(0, duration - start)),
               '-vf', f'fps=1/{step},scale={width}:{height}', '-an', '-sn',
               '-f', 'rawvideo', '-pix_fmt', 'bgr24', 'pipe:1']
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    result = []
    previous = None
    try:
        moment = start
        while moment < duration:
            raw = process.stdout.read(width * height * 3)
            if len(raw) != width * height * 3:
                break
            frame = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            motion = 0.0 if previous is None else float(cv2.absdiff(gray, previous).mean()) / 64
            previous = gray
            result.append((moment, min(1.0, motion), frame))
            moment += step
    finally:
        process.stdout.close()
        if process.poll() is None:
            process.terminate()
        process.wait()
    return result


def detect_camera(path: Path, start: float, end: float) -> dict:
    """Find a face camera and its side; return normalized metadata for Studio."""
    samples = _samples(path, max(end, 1), max(.5, (end - start) / 8), start)
    try:
        import cv2
    except ImportError:
        return {'present': False, 'side': None, 'confidence': 0.0}
    cascade = Path(cv2.data.haarcascades) / 'haarcascade_frontalface_default.xml'
    if not cascade.is_file():
        return {'present': False, 'side': None, 'confidence': 0.0}
    with tempfile.TemporaryDirectory(prefix='clip-haar-') as directory:
        ascii_path = Path(directory) / 'haarcascade.xml'
        shutil.copyfile(cascade, ascii_path)
        detector = cv2.CascadeClassifier(str(ascii_path))
    if detector.empty():
        return {'present': False, 'side': None, 'confidence': 0.0}
    hits = []
    for moment, _, frame in samples:
        if moment < start or moment > end:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = detector.detectMultiScale(gray, 1.1, 5, minSize=(max(24, frame.shape[1] // 14),) * 2)
        if len(faces):
            x, _, w, _ = max(faces, key=lambda item: item[2] * item[3])
            hits.append((x + w / 2) / frame.shape[1])
    if not hits:
        return {'present': False, 'side': None, 'confidence': 0.0}
    center = statistics.median(hits)
    return {'present': True, 'side': 'left' if center < .5 else 'right',
            'confidence': round(min(1.0, len(hits) / 4), 3), 'center_x': round(center, 4)}


def gaming_candidates(path: Path, segments: list[dict], duration: float, count: int,
                      min_seconds: float, max_seconds: float) -> list[dict]:
    stat = path.stat()
    cache_key = stable_id(str(path.resolve()), str(stat.st_size), str(stat.st_mtime_ns),
                          str(duration), str(min_seconds), str(max_seconds), str(len(segments)), str(count))
    cache = settings.clip_storage_root / 'gaming-candidates' / f'{cache_key}.json'
    if cache.is_file():
        return json.loads(cache.read_text(encoding='utf-8'))[:min(count, 12)]
    samples = _samples(path, duration)
    if not samples:
        return []
    ranked = []
    for moment, motion, _ in samples:
        start = max(0.0, moment - min_seconds * .35)
        end = min(duration, start + min(max_seconds, max(min_seconds, 30.0)))
        if end - start < min_seconds and duration >= min_seconds:
            continue
        try:
            loudness = measure_loudness(path, start, min(end, start + 8))
        except Exception:
            loudness = -40.0
        audio = max(0.0, min(1.0, (loudness + 40) / 30))
        text = ' '.join(str(s.get('text', '')) for s in segments
                        if float(s['end']) > start and float(s['start']) < end).strip()
        reaction = min(1.0, sum(len(w) for w in text.split()) / 80)
        score = .5 * motion + .3 * audio + .2 * reaction
        ranked.append({'start': round(start, 3), 'end': round(end, 3), 'score': round(score, 3),
                       'confidence': round(score, 3), 'algorithm': 'gaming-signal-v1',
                       'text': text, 'signals': {'motion': round(motion, 3), 'audio': round(audio, 3),
                                                  'reaction': round(reaction, 3)}})
    chosen = []
    for item in sorted(ranked, key=lambda x: (-x['score'], x['start'])):
        if all(item['end'] <= old['start'] or item['start'] >= old['end'] for old in chosen):
            chosen.append(item)
        if len(chosen) >= min(count, 12):
            break
    chosen = sorted(chosen, key=lambda x: x['start'])
    write_atomic(cache, json.dumps(chosen, ensure_ascii=False).encode('utf-8'))
    return chosen


def vision_review(path: Path, start: float, end: float, text: str) -> dict:
    if not settings.clip_perlapenna_base_url or not settings.clip_perlapenna_api_key:
        raise RuntimeError('Gaming visual review requires CLIP_PERLAPENNA_BASE_URL and CLIP_PERLAPENNA_API_KEY')
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError('Gaming visual review requires OpenCV') from exc
    capture = cv2.VideoCapture(str(path)); frames = []
    try:
        for fraction in (.1, .35, .65, .9):
            capture.set(cv2.CAP_PROP_POS_MSEC, (start + (end - start) * fraction) * 1000)
            ok, frame = capture.read()
            if ok:
                ok, encoded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                if ok:
                    frames.append('data:image/jpeg;base64,' + base64.b64encode(encoded.tobytes()).decode())
    finally:
        capture.release()
    if len(frames) != 4:
        raise RuntimeError('Could not create four preview frames for PerlaPenna')
    content = [{'type': 'text', 'text': 'Assess this gaming moment. Return JSON with approved, score, title (short description of the moment in the language spoken), reason, camera_present, camera_side, crop_points. Speech: ' + text}]
    content += [{'type': 'image_url', 'image_url': {'url': frame}} for frame in frames]
    data = json.dumps({'model': settings.clip_vision_model, 'temperature': 0,
                       'response_format': {'type': 'json_object'},
                       'messages': [{'role': 'user', 'content': content}]}).encode()
    request = Request(settings.clip_perlapenna_base_url.rstrip('/') + '/chat/completions', data=data,
                      headers={'Authorization': f'Bearer {settings.clip_perlapenna_api_key}', 'Content-Type': 'application/json'})
    try:
        with urlopen(request, timeout=120) as response:
            result = json.loads(json.load(response)['choices'][0]['message']['content'])
    except Exception as exc:
        raise RuntimeError(f'PerlaPenna gaming review failed: {exc}') from exc
    points = result.get('crop_points')
    if not isinstance(points, list) or not all(
            isinstance(point, dict) and isinstance(point.get('x'), (int, float))
            and isinstance(point.get('y'), (int, float))
            and 0 <= point['x'] <= 1 and 0 <= point['y'] <= 1 for point in points):
        result['crop_points'] = []
    return result
