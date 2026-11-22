"""Generate a contact sheet for a persisted gaming review job."""
import sys
from pathlib import Path

import cv2
import numpy as np

from clip_service.service import job_view


def main(job_id: str, vertical: bool = False):
    job = job_view(job_id)
    source = Path(job['source_uri'])
    capture = None if vertical else cv2.VideoCapture(str(source))
    rows = []
    try:
        for index, clip in enumerate(job['clips'], 1):
            if vertical and clip['status'] == 'rejected':
                continue
            if vertical:
                preview = Path('gaming-run-storage/work') / f"{clip['id']}-vertical.mp4"
                capture = cv2.VideoCapture(str(preview))
            frames = []
            for fraction in (.1, .5, .9):
                handle = min(15, clip['start']) if vertical else 0
                moment = handle + (clip['end'] - clip['start']) * fraction if vertical else (
                    clip['start'] + (clip['end'] - clip['start']) * fraction)
                capture.set(cv2.CAP_PROP_POS_MSEC, moment * 1000)
                ok, frame = capture.read()
                if not ok:
                    raise RuntimeError(f'Could not read source frame at {moment:.1f}s')
                frame = cv2.resize(frame, (270, 480) if vertical else (480, 270))
                cv2.putText(frame, f'{index}: {moment:.1f}s', (12, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, .8, (255, 255, 255), 2)
                frames.append(frame)
            rows.append(np.hstack(frames))
            if vertical:
                capture.release()
    finally:
        if capture is not None:
            capture.release()
    target = Path('gaming-run-storage/vertical-contact.jpg' if vertical else
                  'gaming-run-storage/review-contact.jpg')
    cv2.imwrite(str(target), np.vstack(rows))
    print(target.resolve())


if __name__ == '__main__':
    main(sys.argv[1], '--vertical' in sys.argv)
