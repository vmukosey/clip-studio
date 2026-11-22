import json
import tempfile
from functools import lru_cache
from pathlib import Path

from .config import settings
from .core import write_atomic
from .gpu import resolve_acceleration
from .media import run


@lru_cache(maxsize=2)
def _model(name: str, device: str, compute_type: str):
    from faster_whisper import WhisperModel
    return WhisperModel(name, device=device, compute_type=compute_type)


def transcribe_clip(path: Path, start: float, end: float, language: str,
                    cache_path: Path, acceleration: dict) -> list[dict]:
    if cache_path.is_file():
        return json.loads(cache_path.read_text(encoding='utf-8'))['segments']
    with tempfile.TemporaryDirectory(prefix='clip-audio-') as directory:
        audio = Path(directory) / 'audio.wav'
        run(['ffmpeg', '-y', '-ss', str(start), '-t', str(end - start), '-i', str(path),
             '-vn', '-ac', '1', '-ar', '16000', str(audio)])
        raw, info = _model(settings.whisper_model, acceleration['whisper_device'],
                           acceleration['whisper_compute_type']).transcribe(
                               str(audio), language=language or None, vad_filter=True, beam_size=5)
        segments = [{'start': round(max(0.0, float(s.start)), 3),
                     'end': round(min(end - start, float(s.end)), 3),
                     'text': s.text.strip()}
                    for s in raw if s.end > s.start and s.text.strip()]
    write_atomic(cache_path, json.dumps({'language': getattr(info, 'language', language),
                                         'segments': segments}, ensure_ascii=False).encode())
    return segments


def transcribe(path: Path, language: str, cache_path: Path, acceleration: dict | None = None) -> list[dict]:
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text(encoding='utf-8'))['segments']
        except (KeyError, TypeError, json.JSONDecodeError):
            cache_path.unlink(missing_ok=True)
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError('faster-whisper is not installed') from exc
    acceleration = acceleration or resolve_acceleration()
    model = WhisperModel(settings.whisper_model, device=acceleration['whisper_device'],
                         compute_type=acceleration['whisper_compute_type'])
    raw_segments, info = model.transcribe(str(path), language=language or None, vad_filter=True,
                                          word_timestamps=False, beam_size=5)
    segments = [{
        'start': round(float(s.start), 3), 'end': round(float(s.end), 3),
        'text': s.text.strip(), 'confidence': round(max(0.0, min(1.0, 1.0 - float(s.no_speech_prob))), 3),
    } for s in raw_segments if s.end > s.start and s.text.strip()]
    payload = {'language': getattr(info, 'language', language), 'model': settings.whisper_model,
               'whisper_device': acceleration['whisper_device'],
               'whisper_compute_type': acceleration['whisper_compute_type'],
               'segments': segments}
    write_atomic(cache_path, json.dumps(payload, ensure_ascii=False, indent=2).encode())
    return segments
