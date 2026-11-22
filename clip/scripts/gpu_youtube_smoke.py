"""Run the real YouTube -> CUDA Whisper -> NVENC pipeline in an isolated directory."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path


# Allow direct execution as ``python scripts/gpu_youtube_smoke.py`` from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


DEFAULT_URL = 'https://www.youtube.com/watch?v=MnO83la9uWc'


def _sample_gpu(samples: list[dict], stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            result = subprocess.run(
                ['nvidia-smi', '--query-gpu=timestamp,utilization.gpu,utilization.memory,memory.used',
                 '--format=csv,noheader,nounits'], check=True, capture_output=True, text=True, timeout=5)
            values = [value.strip() for value in result.stdout.strip().split(',')]
            if len(values) == 4:
                samples.append({'timestamp': values[0], 'gpu_percent': int(values[1]),
                                'memory_percent': int(values[2]), 'memory_mib': int(values[3])})
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            samples.append({'error': f'{type(exc).__name__}: {exc}'})
        stop.wait(.5)


def _minimal_nvenc_test(root: Path) -> None:
    target = root / 'nvenc-preflight.mp4'
    subprocess.run(['ffmpeg', '-y', '-f', 'lavfi', '-i', 'color=size=256x256:rate=1:color=black', '-t', '1',
                    '-c:v', 'h264_nvenc', '-pix_fmt', 'yuv420p', str(target)], check=True,
                   capture_output=True, text=True, timeout=60)
    if not target.is_file() or not target.stat().st_size:
        raise RuntimeError('NVENC preflight did not create a video')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default=DEFAULT_URL)
    parser.add_argument('--storage-root', type=Path, default=Path('storage-gpu-smoke'))
    parser.add_argument('--max-seconds', type=float, default=None,
                        help='trim the downloaded source before processing (for example 300)')
    args = parser.parse_args()
    root = args.storage_root.resolve()
    root.mkdir(parents=True, exist_ok=True)

    # Settings and the SQLite engine are initialized during these imports.
    os.environ['CLIP_STORAGE_ROOT'] = str(root)
    os.environ['DATABASE_URL'] = f'sqlite:///{(root / "gpu-smoke.db").as_posix()}'
    os.environ['CLIP_REQUIRE_GPU'] = 'true'
    os.environ['CLIP_WHISPER_DEVICE'] = 'cuda'
    os.environ['CLIP_WHISPER_COMPUTE_TYPE'] = 'int8_float16'
    os.environ['CLIP_VIDEO_ENCODER'] = 'h264_nvenc'

    from clip_service.db import init_db
    from clip_service.gpu import preflight, resolve_acceleration
    from clip_service.media import prepare_source, probe_video, run
    from clip_service.transcription import transcribe
    from clip_service.service import create_job, create_source, process_job

    capabilities = preflight()
    acceleration = resolve_acceleration(capabilities)
    _minimal_nvenc_test(root)
    samples: list[dict] = []
    stopped = threading.Event()
    monitor = threading.Thread(target=_sample_gpu, args=(samples, stopped), daemon=True)
    started = time.monotonic()
    monitor.start()
    try:
        init_db()
        source = create_source(args.url, 'youtube')
        if args.max_seconds:
            source_path, _ = prepare_source(source)
            trimmed = source_path.with_name('source.trimmed.mp4')
            run(['ffmpeg', '-y', '-i', str(source_path), '-t', str(args.max_seconds), '-c', 'copy', str(trimmed)])
            trimmed.replace(source_path)
            (source_path.parent / 'probe.json').unlink(missing_ok=True)
            # Keep the CUDA transcription assertion while using deterministic windows
            # for the shortened export-format smoke.
            transcribe(source_path, 'ru', source_path.parent / 'gpu-smoke-transcript.json', acceleration)
            window = min(90.0, args.max_seconds / 3.0)
            gap = max(0.0, (args.max_seconds - window * 3) / 2.0)
            intervals = [{'start': round(index * (window + gap), 3),
                          'end': round(index * (window + gap) + window, 3)} for index in range(3)]
        else:
            intervals = None
        config = {'count': 3, 'language': 'ru', 'template': 'gpu-youtube-smoke'}
        if intervals:
            config['intervals'] = intervals
        job = create_job(source.id, config)
        result = process_job(job.id)
    finally:
        stopped.set()
        monitor.join(timeout=3)
    elapsed = round(time.monotonic() - started, 3)

    if result['status'] != 'exported' or len(result['clips']) != 3:
        raise RuntimeError(f'expected three exported clips, received: {result}')
    manifests = []
    for clip in result['clips']:
        artifacts = {name: Path(value) for name, value in clip['artifacts'].items()}
        if not all(path.is_file() and path.stat().st_size for path in artifacts.values()):
            raise RuntimeError(f'missing artifact for clip {clip["id"]}')
        manifest = json.loads(artifacts['manifest'].read_text(encoding='utf-8'))
        output = probe_video(artifacts['mp4'])
        if (output['width'], output['height'], output['codec_name']) != (1080, 1920, 'h264'):
            raise RuntimeError(f'invalid output probe: {output}')
        if manifest['whisper_device'] != 'cuda' or manifest['video_encoder'] != 'h264_nvenc':
            raise RuntimeError(f'GPU backend fallback detected: {manifest}')
        manifests.append({'clip_id': clip['id'], 'artifacts': {k: str(v) for k, v in artifacts.items()},
                          'duration': output['duration']})
    if not any(sample.get('gpu_percent', 0) > 0 or sample.get('memory_mib', 0) > 0 for sample in samples):
        raise RuntimeError('nvidia-smi did not report GPU activity')
    report = {'url': args.url, 'max_seconds': args.max_seconds, 'elapsed_seconds': elapsed,
              'acceleration': acceleration,
              'clips': manifests, 'gpu_telemetry': samples}
    report_path = root / 'gpu-youtube-smoke-report.json'
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'report': str(report_path), **report}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
