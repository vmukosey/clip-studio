"""GPU capability detection and deterministic backend selection."""

from __future__ import annotations

import subprocess
import os
from pathlib import Path
import shutil
import sys
from functools import lru_cache

from .config import settings


def _ensure_local_ffmpeg_path() -> None:
    """Prefer the project-local Windows bundle installed by the GPU setup."""
    paths = []
    runtime = Path(sys.executable).resolve().parent.parent
    paths.extend([runtime / 'Lib' / 'site-packages' / 'nvidia' / 'cublas' / 'bin',
                  runtime / 'Lib' / 'site-packages' / 'nvidia' / 'cuda_nvrtc' / 'bin',
                  runtime / 'Lib' / 'site-packages' / 'nvidia' / 'cudnn' / 'bin'])
    root = Path(__file__).resolve().parents[1] / 'tools' / 'ffmpeg'
    paths.extend(root.glob('*/bin'))
    existing = os.environ.get('PATH', '')
    for path in reversed([p for p in paths if p.is_dir()]):
        if str(path) not in existing:
            existing = str(path) + os.pathsep + existing
    os.environ['PATH'] = existing


_ensure_local_ffmpeg_path()


class AcceleratorError(RuntimeError):
    pass


def _command_output(command: list[str]) -> tuple[bool, str]:
    try:
        completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=15)
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    output = (completed.stdout or '') + (completed.stderr or '')
    return completed.returncode == 0, output.strip()


@lru_cache(maxsize=1)
def preflight() -> dict:
    """Report only capabilities that the worker can actually use."""
    cuda_devices = 0
    cuda_error = ''
    try:
        import ctranslate2
        cuda_devices = int(ctranslate2.get_cuda_device_count())
    except Exception as exc:  # optional acceleration dependency and driver errors
        cuda_error = f'{type(exc).__name__}: {exc}'

    ffmpeg_ok, encoders = _command_output(['ffmpeg', '-hide_banner', '-encoders'])
    nvenc = ffmpeg_ok and any('h264_nvenc' in line for line in encoders.splitlines())
    nvenc_error = '' if nvenc else ('h264_nvenc is absent from ffmpeg' if ffmpeg_ok else encoders)
    return {
        'cuda': cuda_devices > 0,
        'cuda_device_count': cuda_devices,
        'cuda_error': cuda_error,
        'nvenc': nvenc,
        'nvenc_error': nvenc_error,
        'ffmpeg': ffmpeg_ok,
    }


def reset_preflight_cache():
    """Test helper for changed process-level capability mocks."""
    preflight.cache_clear()


def resolve_acceleration(capabilities: dict | None = None) -> dict:
    """Resolve configured preferences once, enforcing strict GPU policy when requested."""
    capabilities = capabilities or preflight()
    configured_device = settings.clip_whisper_device.lower()
    if configured_device not in {'auto', 'cpu', 'cuda'}:
        raise AcceleratorError('CLIP_WHISPER_DEVICE must be auto, cpu, or cuda')
    configured_encoder = settings.clip_video_encoder.lower()
    if configured_encoder not in {'auto', 'libx264', 'h264_nvenc'}:
        raise AcceleratorError('CLIP_VIDEO_ENCODER must be auto, libx264, or h264_nvenc')

    whisper_device = ('cuda' if capabilities['cuda'] else 'cpu') if configured_device == 'auto' else configured_device
    video_encoder = ('h264_nvenc' if capabilities['nvenc'] else 'libx264') if configured_encoder == 'auto' else configured_encoder
    configured_compute = settings.clip_whisper_compute_type.lower()
    if configured_compute == 'auto':
        whisper_compute_type = 'int8_float16' if whisper_device == 'cuda' else 'int8'
    else:
        whisper_compute_type = configured_compute

    if whisper_device == 'cuda' and not capabilities['cuda']:
        raise AcceleratorError(f'CUDA requested but unavailable: {capabilities["cuda_error"] or "no CUDA devices"}')
    if video_encoder == 'h264_nvenc' and not capabilities['nvenc']:
        raise AcceleratorError(f'NVENC requested but unavailable: {capabilities["nvenc_error"] or "h264_nvenc unavailable"}')
    if settings.clip_require_gpu and (whisper_device != 'cuda' or video_encoder != 'h264_nvenc'):
        raise AcceleratorError('CLIP_REQUIRE_GPU=true requires CUDA Whisper and h264_nvenc')

    return {
        'whisper_device': whisper_device,
        'whisper_compute_type': whisper_compute_type,
        'video_encoder': video_encoder,
        'require_gpu': settings.clip_require_gpu,
        'capabilities': capabilities,
    }
