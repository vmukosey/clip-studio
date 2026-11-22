"""Isolated Piper invocation; also handles eSpeak's Windows Unicode path issue."""

import shutil
import sys
import tempfile
import wave
from pathlib import Path

from piper import PiperVoice
from piper.phonemize_espeak import ESPEAK_DATA_DIR


def main():
    data = ESPEAK_DATA_DIR
    if not str(data).isascii():
        data = Path(tempfile.gettempdir()) / 'clip-piper-espeak-ng-data'
        if not (data / 'phontab').is_file():
            shutil.copytree(ESPEAK_DATA_DIR, data, dirs_exist_ok=True)
    voice = PiperVoice.load(Path(sys.argv[1]), espeak_data_dir=data)
    with wave.open(sys.argv[2], 'wb') as output:
        voice.synthesize_wav(sys.stdin.read(), output)


if __name__ == '__main__':
    main()
