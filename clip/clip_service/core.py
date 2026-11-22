import hashlib
import json
import os
import tempfile
from pathlib import Path


def stable_id(*parts: str) -> str:
    return hashlib.sha256(':'.join(parts).encode()).hexdigest()[:24]

def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))

def naming(job_id: str, clip_id: str, ext: str = 'mp4') -> str:
    return f'{job_id}_{clip_id}.{ext}'

def write_atomic(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_name = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f'.{path.name}.',
                                         suffix='.tmp', delete=False) as tmp:
            tmp_name = tmp.name
            tmp.write(data)
            tmp.flush()
            os.fsync(tmp.fileno())
        Path(tmp_name).replace(path)
    finally:
        if tmp_name:
            Path(tmp_name).unlink(missing_ok=True)

def srt_timestamp(seconds: float) -> str:
    ms = round(max(0, seconds) * 1000)
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f'{h:02d}:{m:02d}:{s:02d},{ms:03d}'

def vtt_timestamp(seconds: float) -> str:
    return srt_timestamp(seconds).replace(',', '.')

def subtitles(segments: list[dict], fmt: str = 'srt') -> str:
    out = []
    for i, seg in enumerate(segments, 1):
        ts = f"{srt_timestamp(seg['start'])} --> {srt_timestamp(seg['end'])}" if fmt == 'srt' else f"{vtt_timestamp(seg['start'])} --> {vtt_timestamp(seg['end'])}"
        out.append(f'{i}\n{ts}\n{seg.get("text", "").strip()}\n')
    return ('WEBVTT\n\n' if fmt == 'vtt' else '') + '\n'.join(out)

def deterministic_candidates(duration: float, count: int, min_seconds: int, max_seconds: int):
    if duration <= 0: return []
    length = min(duration, max_seconds, max(min_seconds, duration / max(count, 1)))
    count = max(1, min(count, int(duration // length) or 1))
    step = 0 if count == 1 else (duration - length) / (count - 1)
    return [{'start': round(i * step, 3), 'end': round(min(duration, i * step + length), 3), 'score': round(1 - i / max(count, 1), 3), 'confidence': 0.5, 'algorithm': 'uniform-v2'} for i in range(count)]
