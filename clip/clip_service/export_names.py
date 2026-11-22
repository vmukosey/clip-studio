import re
import json
from pathlib import Path

from .config import settings


def safe_name(value: str, fallback: str = 'Untitled', limit: int = 72) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', ' ', value)
    value = re.sub(r'\s+', ' ', value).strip(' .')[:limit].rstrip(' .')
    if not value or value.upper().split('.')[0] in {'CON', 'PRN', 'AUX', 'NUL',
            *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}:
        return fallback
    return value


def export_dir(source, job) -> Path:
    stem = Path(source.uri).stem
    if source.kind == 'youtube':
        metadata = settings.clip_storage_root / 'sources' / source.id / 'metadata.json'
        if metadata.is_file():
            stem = json.loads(metadata.read_text(encoding='utf-8')).get('title') or stem
    source_name = safe_name(stem, 'Source', 64)
    date = job.created_at.strftime('%Y-%m-%d')
    kind = 'manual' if job.config.get('intervals') is not None else job.config.get('template', 'default')
    return (settings.clip_storage_root / 'exports' /
            f'{source_name}-{source.id[:8]}' / f'{date}-{safe_name(kind)}-{job.id[:8]}')


def available_base(directory: Path, number: int, title: str, exclude: set[Path] | None = None) -> str:
    prefix = f'{number:02d} - {safe_name(title, f"Clip {number:02d}")}'
    excluded = {path.resolve() for path in (exclude or set())}
    candidate = prefix
    suffix = 2
    while directory.is_dir() and any(
            path.stem.casefold() == candidate.casefold() and path.resolve() not in excluded
            for path in directory.iterdir() if path.is_file()):
        candidate = f'{prefix} ({suffix})'
        suffix += 1
    return candidate
