"""Verify a completed job and optionally create start/middle/end contact sheets."""
import argparse
import json
from pathlib import Path

from clip_service.media import probe_video
from clip_service.service import job_view


def verify(job_id: str, contact_dir: Path | None = None) -> int:
    job = job_view(job_id)
    if not job:
        raise ValueError('job not found')
    errors = []
    samples = {'start': [], 'middle': [], 'end': []}
    for number, clip in enumerate(job['clips'], 1):
        name = f'{number:02d}'
        paths = {kind: Path(path) for kind, path in clip['artifacts'].items()}
        if clip['status'] != 'exported':
            errors.append(f'{name}: status {clip["status"]}')
            continue
        if set(paths) != {'mp4', 'thumbnail', 'srt', 'vtt', 'manifest'}:
            errors.append(f'{name}: incomplete artifact mapping')
            continue
        if any(not path.is_file() for path in paths.values()):
            errors.append(f'{name}: artifact path missing')
            continue
        if len({path.stem for path in paths.values()}) != 1 or not paths['mp4'].stem.startswith(f'{name} - '):
            errors.append(f'{name}: filename differs from source order')
        manifest = json.loads(paths['manifest'].read_text(encoding='utf-8'))
        if manifest.get('artifacts') != clip['artifacts'] or manifest.get('status') != 'ready':
            errors.append(f'{name}: manifest differs from database')
        if manifest.get('selection') != clip['selection']:
            errors.append(f'{name}: selection differs from manifest')
        probe = probe_video(paths['mp4'])
        if (probe['codec_name'], probe['width'], probe['height'], probe['audio_codec_name']) != (
                'h264', 1080, 1920, 'aac'):
            errors.append(f'{name}: unexpected codec, size, or audio')
        if abs(probe['duration'] - (clip['end'] - clip['start'])) > 1:
            errors.append(f'{name}: unexpected duration')
        if contact_dir:
            import cv2
            capture = cv2.VideoCapture(str(paths['mp4']))
            for label, fraction in (('start', .08), ('middle', .5), ('end', .92)):
                capture.set(cv2.CAP_PROP_POS_MSEC, probe['duration'] * fraction * 1000)
                ok, frame = capture.read()
                if not ok:
                    errors.append(f'{name}: unreadable {label} frame')
                    continue
                tile = cv2.resize(frame, (216, 384))
                cv2.rectangle(tile, (0, 0), (216, 30), (15, 20, 24), -1)
                cv2.putText(tile, f'{name} {label} {clip["start"]:.1f}-{clip["end"]:.1f}',
                            (6, 20), cv2.FONT_HERSHEY_SIMPLEX, .43, (245, 245, 245), 1)
                samples[label].append(tile)
            capture.release()
    if contact_dir:
        import cv2
        import numpy as np
        contact_dir.mkdir(parents=True, exist_ok=True)
        for label, frames in samples.items():
            if frames:
                rows = []
                for offset in range(0, len(frames), 5):
                    row = frames[offset:offset + 5]
                    row.extend([np.zeros_like(row[0]) for _ in range(5 - len(row))])
                    rows.append(np.hstack(row))
                cv2.imwrite(str(contact_dir / f'{job_id}-{label}.jpg'), np.vstack(rows))
    print(json.dumps({'job_id': job_id, 'clips': len(job['clips']), 'errors': errors,
                      'contact_dir': str(contact_dir) if contact_dir else None}, ensure_ascii=False))
    return 1 if errors else 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('job_id')
    parser.add_argument('--contact-dir', type=Path)
    args = parser.parse_args()
    raise SystemExit(verify(args.job_id, args.contact_dir))
