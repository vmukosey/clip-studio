"""Rebuild the two approved layout examples from the local gaming run."""
import json
import os
import sqlite3
import sys
from pathlib import Path

from clip_service.composition import render_vertical
from clip_service.media import probe_video


ROOT = Path(__file__).resolve().parents[1]
FFMPEG = next((ROOT / 'tools' / 'ffmpeg').glob('*/bin'))
os.environ['PATH'] = str(FFMPEG) + os.pathsep + os.environ['PATH']
SOURCE = ROOT.parent / 'клипы' / 'GTA 6 WTF and Funny Moments! #1 [IO9NlaC1yGw].mp4'
EXAMPLES = {'09079d380597593f70522c5b': 'no-camera',
            'd37d27df715f695bd56acc87': 'camera-left'}


def main():
    target = ROOT / 'gaming-layout-review'
    target.mkdir(exist_ok=True)
    with sqlite3.connect(ROOT / 'gaming-run.db') as db:
        rows = db.execute('select id,start,end,review,artifacts from clips').fetchall()
    probe = probe_video(SOURCE)
    verify_cache = '--verify-cache' in sys.argv
    selected = set(sys.argv[1:]) - {'--verify-cache'} or set(EXAMPLES.values())
    for clip_id, name in EXAMPLES.items():
        if name not in selected:
            continue
        row = next(row for row in rows if row[0] == clip_id)
        _, start, end, review_raw, artifacts_raw = row
        review = json.loads(review_raw)
        artifacts = json.loads(artifacts_raw)
        srt = Path(artifacts['srt'])
        layout = {**review['layout'], 'gaming': True, 'crop_points': review.get('crop_points', [])}
        result = render_vertical(SOURCE, probe, start, end, srt,
                                 target / f'{name}.mp4', target / f'{name}.jpg',
                                 layout=layout, acceleration={'video_encoder': 'libx264'},
                                 force=not verify_cache)
        if verify_cache and result['crop_mode'] != 'cached':
            raise RuntimeError(f'{name} did not use the render cache')
        print(name, result, flush=True)

    import cv2
    import numpy as np
    sheets = []
    for name in EXAMPLES.values():
        capture = cv2.VideoCapture(str(target / f'{name}.mp4'))
        duration = probe_video(target / f'{name}.mp4')['duration']
        frames = []
        try:
            for fraction in (.1, .5, .9):
                capture.set(cv2.CAP_PROP_POS_MSEC, duration * fraction * 1000)
                ok, frame = capture.read()
                if not ok:
                    raise RuntimeError(f'Could not read {name} at {fraction:.0%}')
                frames.append(cv2.resize(frame, (270, 480)))
        finally:
            capture.release()
        sheets.append(np.hstack(frames))
    ok, encoded = cv2.imencode('.jpg', np.vstack(sheets))
    if not ok:
        raise RuntimeError('Could not encode review contact sheet')
    (target / 'contact.jpg').write_bytes(encoded.tobytes())


if __name__ == '__main__':
    main()
