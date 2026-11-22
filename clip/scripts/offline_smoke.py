import json
from pathlib import Path

from clip_service.config import settings
from clip_service.db import init_db
from clip_service.media import probe_video, run
from clip_service.service import create_job, create_source, process_job


def main():
    root = settings.clip_storage_root
    root.mkdir(parents=True, exist_ok=True)
    source_path = root / 'smoke-source.mp4'
    ad_path = root / 'smoke-ad.mp4'
    if not source_path.exists():
        run(['ffmpeg', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=30',
             '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=44100', '-t', '6',
             '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(source_path)])
    if not ad_path.exists():
        run(['ffmpeg', '-y', '-f', 'lavfi', '-i', 'color=red:size=1080x1920:rate=30',
             '-t', '1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(ad_path)])
    init_db()
    source = create_source(str(source_path), 'local')
    job = create_job(source.id, {'count': 1, 'language': 'ru', 'template': 'smoke',
                                 'intervals': [{'start': .5, 'end': 4.5}],
                                 'campaign_rules': {'banner': {'text': 'Smoke', 'height': 100},
                                                    'watermark': {'text': '@clip'},
                                                    'fullscreen': [{'path': str(ad_path), 'start': 1,
                                                                    'duration': 1, 'priority': 10}]}})
    first = process_job(job.id)
    second = process_job(job.id)
    assert first['status'] == second['status'] == 'exported'
    assert [c['id'] for c in first['clips']] == [c['id'] for c in second['clips']]
    clip = first['clips'][0]
    artifacts = {name: Path(path) for name, path in clip['artifacts'].items()}
    assert all(path.is_file() and path.stat().st_size for path in artifacts.values())
    output_probe = probe_video(artifacts['mp4'])
    assert (output_probe['width'], output_probe['height']) == (1080, 1920)
    assert abs(output_probe['duration'] - 4.0) < .25
    manifest = json.loads(artifacts['manifest'].read_text(encoding='utf-8'))
    assert manifest['start'] == .5 and manifest['end'] == 4.5
    assert manifest['composition']['ads'] == 1
    print(json.dumps({'job_id': job.id, 'clip_id': clip['id'], 'probe': output_probe,
                      'artifacts': {key: str(value) for key, value in artifacts.items()}}, indent=2))


if __name__ == '__main__':
    main()
