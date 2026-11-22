"""Create timestamped source contact sheets for manual storyboard ground truth."""
import math
from pathlib import Path

import cv2
import numpy as np

from storyboard_fps_experiment import OUTPUT, SCENES, SOURCE


def main():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    capture = cv2.VideoCapture(str(SOURCE))
    if not capture.isOpened():
        raise RuntimeError('Cannot open source video')
    try:
        for name, (start, end) in SCENES.items():
            fps = 4 if name in ('short_action', 'bigfoot') else 1
            times = [start + i / fps for i in range(math.ceil((end - start) * fps))]
            width, height, columns = 320, 180, 6
            sheet = np.full(((height + 24) * math.ceil(len(times) / columns),
                             width * columns, 3), 255, dtype=np.uint8)
            for index, at in enumerate(times):
                capture.set(cv2.CAP_PROP_POS_MSEC, at * 1000)
                ok, frame = capture.read()
                if not ok:
                    raise RuntimeError(f'Cannot decode {at:.2f}s')
                frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
                x, y = index % columns * width, index // columns * (height + 24)
                sheet[y:y + height, x:x + width] = frame
                cv2.putText(sheet, f'{at:.2f}s', (x + 5, y + height + 17),
                            cv2.FONT_HERSHEY_SIMPLEX, .5, (0, 0, 0), 1, cv2.LINE_AA)
            path = OUTPUT.parent / f'reference-{name}.jpg'
            ok, encoded = cv2.imencode('.jpg', sheet, [cv2.IMWRITE_JPEG_QUALITY, 88])
            if not ok:
                raise RuntimeError(f'Cannot encode {name} contact sheet')
            path.write_bytes(encoded.tobytes())
            print(path)
    finally:
        capture.release()


if __name__ == '__main__':
    main()
