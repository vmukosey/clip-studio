import json
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from .config import settings
from .core import stable_id, write_atomic


class MediaError(RuntimeError):
    pass


def require_free_space(path: Path, required_mb: int | None = None):
    path.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(path).free
    required = (required_mb or settings.clip_min_free_mb) * 1024 * 1024
    if free < required:
        raise MediaError(f'insufficient disk space: {free // 1024 // 1024} MiB free')


def run(command: list[str], timeout: int | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, check=True, capture_output=True, text=True,
                              timeout=timeout or settings.clip_ffmpeg_timeout)
    except FileNotFoundError as exc:
        raise MediaError(f'{command[0]} is not installed') from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f'{command[0]} timed out') from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or '').strip()[-2000:]
        raise MediaError(f'{command[0]} failed: {detail}') from exc


def probe_video(path: Path) -> dict:
    result = run([
        'ffprobe', '-v', 'error', '-show_streams', '-show_format',
        '-of', 'json', str(path),
    ])
    data = json.loads(result.stdout)
    video = next((s for s in data.get('streams', []) if s.get('codec_type') == 'video'), None)
    audio = next((s for s in data.get('streams', []) if s.get('codec_type') == 'audio'), None)
    if not video:
        raise MediaError('source has no video stream')
    duration = float(data.get('format', {}).get('duration') or video.get('duration') or 0)
    if duration <= 0:
        raise MediaError('source duration is unavailable')
    return {
        'duration': duration,
        'width': int(video.get('width') or 0),
        'height': int(video.get('height') or 0),
        'codec_name': video.get('codec_name', ''),
        'has_audio': audio is not None,
        'audio_codec_name': audio.get('codec_name', '') if audio else '',
        'format': data.get('format', {}).get('format_name', ''),
    }


def measure_loudness(path: Path, start: float, end: float) -> float:
    result = run(['ffmpeg', '-ss', str(start), '-t', str(max(.1, end - start)), '-i', str(path),
                  '-vn', '-af', 'volumedetect', '-f', 'null', '-'])
    match = re.search(r'mean_volume:\s*(-?[0-9.]+) dB', result.stderr or '')
    return float(match.group(1)) if match else -40.0


def _is_youtube_url(uri: str) -> bool:
    parsed = urlparse(uri)
    host = (parsed.hostname or '').lower()
    return parsed.scheme in {'http', 'https'} and (host == 'youtu.be' or host == 'youtube.com' or host.endswith('.youtube.com'))


def prepare_source(source) -> tuple[Path, dict]:
    cache_dir = settings.clip_storage_root / 'sources' / source.id
    cache_dir.mkdir(parents=True, exist_ok=True)
    probe_path = cache_dir / 'probe.json'
    existing = next((p for p in sorted(cache_dir.glob('source.*')) if p.is_file()), None)

    if source.kind == 'local':
        path = Path(source.uri).expanduser().resolve()
        if path.suffix.lower() != '.mp4' or not path.is_file():
            raise MediaError('local source must be an existing MP4 file')
        if path.stat().st_size > settings.clip_max_upload_mb * 1024 * 1024:
            raise MediaError('source exceeds configured size limit')
    elif source.kind == 'youtube':
        if not _is_youtube_url(source.uri):
            raise MediaError('only youtube.com and youtu.be URLs are supported')
        path = existing or cache_dir / 'source.mp4'
        if not path.exists():
            require_free_space(cache_dir)
            try:
                from yt_dlp import YoutubeDL
            except ImportError as exc:
                raise MediaError('yt-dlp is not installed') from exc
            options = {
                # Android exposes a directly downloadable progressive MP4 when web SABR
                # formats require a PO token (common in headless smoke environments).
                'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/18/best',
                'extractor_args': {'youtube': {'player_client': ['android']}},
                'outtmpl': str(cache_dir / 'source.%(ext)s'),
                'merge_output_format': 'mp4',
                'noplaylist': True,
                'socket_timeout': settings.clip_download_timeout,
                'max_filesize': settings.clip_max_upload_mb * 1024 * 1024,
                'quiet': True,
            }
            try:
                with YoutubeDL(options) as ydl:
                    info = ydl.extract_info(source.uri, download=True)
                    path = Path(ydl.prepare_filename(info)).with_suffix('.mp4')
                    write_atomic(cache_dir / 'metadata.json', json.dumps({
                        'title': info.get('title') or '', 'id': info.get('id') or ''},
                        ensure_ascii=False).encode('utf-8'))
            except Exception as exc:
                raise MediaError(f'YouTube download failed: {exc}') from exc
            if not path.exists():
                path = next((p for p in cache_dir.glob('source.*') if p.is_file()), path)
    else:
        raise MediaError(f'unsupported source kind: {source.kind}')

    signature = {'path': str(path), 'size': path.stat().st_size, 'mtime_ns': path.stat().st_mtime_ns}
    if probe_path.exists():
        cached = json.loads(probe_path.read_text(encoding='utf-8'))
        if cached.get('signature') == signature:
            return path, cached['probe']
    try:
        probe = probe_video(path)
    except MediaError:
        if source.kind == 'youtube' and existing is not None:
            existing.unlink(missing_ok=True)
            probe_path.unlink(missing_ok=True)
            return prepare_source(source)
        raise
    write_atomic(probe_path, json.dumps({'signature': signature, 'probe': probe}, indent=2).encode())
    return path, probe


def upload_target(filename: str) -> Path:
    suffix = Path(filename or '').suffix.lower()
    if suffix != '.mp4':
        raise MediaError('only MP4 uploads are supported')
    return settings.clip_storage_root / 'sources' / f'{stable_id(filename)}.mp4'
