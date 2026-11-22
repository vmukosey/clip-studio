"""Compare storyboard frame rates on fixed scenes from the gaming source.

Run from clip/ with CLIP_PERLAPENNA_API_KEY and CLIP_PERLAPENNA_BASE_URL set.
Results are resumable and contain no credentials.
"""
import argparse
import base64
from http.client import RemoteDisconnected
import json
import math
import socket
import ssl
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clip_service.config import settings


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'gaming-run-storage/sources/770fc0da6d5c7b94ff7909d2/studio-preview.mp4'
TRANSCRIPT = ROOT / 'gaming-run-storage/sources/770fc0da6d5c7b94ff7909d2/transcript-2c940391f43af20dd5263641.json'
OUTPUT = ROOT.parent / 'audit/results/storyboard-fps-experiment.json'
SCENES = {
    'short_action': (0, 9),
    'bigfoot': (22, 44),
    'scooter': (63, 86),
    'plane_landing': (86, 112),
    'shark': (123, 151),
    'social_feed': (208, 228),
    'plane_crash': (299, 320),
    'carjacking': (435, 474),
    'police': (487, 501),
    'gas_station_crash': (530, 553),
}
MODES = {'current': (0.2, 320, 180), '1fps_180p': (1, 320, 180),
         '1fps': (1, 640, 360), '2fps': (2, 640, 360),
         '3fps': (3, 640, 360), '4fps': (4, 640, 360)}


def frames_at(capture, times, width, height):
    frames = []
    for at in times:
        capture.set(cv2.CAP_PROP_POS_MSEC, at * 1000)
        ok, frame = capture.read()
        if not ok:
            raise RuntimeError(f'Cannot decode frame at {at:.2f}s')
        frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
        ok, encoded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 72])
        if not ok:
            raise RuntimeError(f'Cannot encode frame at {at:.2f}s')
        frames.append({'at': round(at, 3), 'url': 'data:image/jpeg;base64,' +
                       base64.b64encode(encoded.tobytes()).decode('ascii')})
    return frames


def ask(prompt, frames):
    content = [{'type': 'text', 'text': prompt}]
    for frame in frames:
        content.append({'type': 'text', 'text': f"Frame at {frame['at']:.3f}s:"})
        content.append({'type': 'image_url', 'image_url': {'url': frame['url']}})
    payload = {'model': settings.clip_vision_model, 'temperature': 0,
               'response_format': {'type': 'json_object'},
               'messages': [{'role': 'user', 'content': content}]}
    request = Request(settings.clip_perlapenna_base_url.rstrip('/') + '/chat/completions',
                      data=json.dumps(payload).encode(),
                      headers={'Authorization': f'Bearer {settings.clip_perlapenna_api_key}',
                               'Content-Type': 'application/json'})
    started = time.monotonic()
    for attempt in range(4):
        try:
            with urlopen(request, timeout=180) as response:
                result = json.load(response)
            break
        except HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(2 ** attempt * 3)
                continue
            raise RuntimeError(f'Vision HTTP {exc.code}: {exc.read().decode(errors="replace")[:300]}') from exc
        except (URLError, RemoteDisconnected, socket.timeout, ssl.SSLError) as exc:
            if attempt < 3:
                time.sleep(2 ** attempt * 3)
                continue
            raise RuntimeError(f'Vision connection failed: {type(exc).__name__}') from exc
    choice = result['choices'][0]['message']['content']
    return {'answer': json.loads(choice), 'seconds': round(time.monotonic() - started, 3),
            'usage': result.get('usage', {})}


def time_ranges(start, end, mode):
    if mode == 'current':
        return [(at, min(686.01, at + 40))
                for at in range(int(start // 40) * 40, int(math.ceil(end / 40)) * 40, 40)]
    ranges = []
    at = start
    while at < end:
        stop = min(end, at + 6)
        ranges.append((at, stop))
        if stop == end:
            break
        at = stop - 1
    return ranges


def frame_times(start, end, fps, mode):
    if mode == 'current':
        return [start + i * 5 for i in range(8) if start + i * 5 < end]
    count = math.ceil((end - start) * fps)
    return [start + i / fps for i in range(count) if start + i / fps < end]


def speech_in(segments, start, end):
    return [{'start': s['start'], 'end': s['end'], 'text': s['text']}
            for s in segments if s['end'] > start and s['start'] < end]


def save(data):
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(OUTPUT)


def run(scene_filter=None, mode_filter=None):
    if not settings.clip_perlapenna_base_url or not settings.clip_perlapenna_api_key:
        raise RuntimeError('Set CLIP_PERLAPENNA_BASE_URL and CLIP_PERLAPENNA_API_KEY in this process')
    segments = json.loads(TRANSCRIPT.read_text(encoding='utf-8'))['segments']
    data = json.loads(OUTPUT.read_text(encoding='utf-8')) if OUTPUT.exists() else {}
    if data and data.get('model') != settings.clip_vision_model:
        raise RuntimeError('Existing results use a different model')
    data.setdefault('model', settings.clip_vision_model)
    data.setdefault('source', SOURCE.name)
    data.setdefault('scenes', {})
    capture = cv2.VideoCapture(str(SOURCE))
    if not capture.isOpened():
        raise RuntimeError('Cannot open source video')
    try:
        for scene, (start, end) in SCENES.items():
            if scene_filter and scene != scene_filter:
                continue
            scene_data = data['scenes'].setdefault(scene, {'start': start, 'end': end, 'modes': {}})
            for mode, (fps, width, height) in MODES.items():
                if mode_filter and mode != mode_filter:
                    continue
                if scene_data['modes'].get(mode, {}).get('protocol') != 2:
                    scene_data['modes'][mode] = {'protocol': 2, 'fps': fps,
                                                  'size': [width, height], 'blocks': []}
                mode_data = scene_data['modes'][mode]
                ranges = time_ranges(start, end, mode)
                for index, (block_start, block_end) in enumerate(ranges):
                    if index < len(mode_data['blocks']):
                        continue
                    times = frame_times(block_start, block_end, fps, mode)
                    frames = frames_at(capture, times, width, height)
                    speech = speech_in(segments, block_start, block_end)
                    prompt = (f'Analyze the video from {block_start:.2f} to {block_end:.2f}s. '
                              'List every visible action and scene change in time order, including brief actions. '
                              'Keep visible observations separate from spoken words. Do not infer an object or '
                              'person solely from dialogue. State uncertainty explicitly. '
                              'Return JSON with events array; each event has at (seconds), visible, spoken, '
                              'uncertainty strings. The frame following each timestamp is sampled at that time. '
                              f'Transcript: {json.dumps(speech, ensure_ascii=False)}')
                    try:
                        result = ask(prompt, frames)
                    except RuntimeError as exc:
                        if len(frames) <= 4 or mode == 'current':
                            raise
                        midpoint = block_start + (block_end - block_start) / 2
                        smaller = []
                        for a, b in ((block_start, midpoint), (midpoint, block_end)):
                            chunk_frames = [f for f in frames if a <= f['at'] < b]
                            smaller.append(ask(prompt.replace(f'{block_start:.2f} to {block_end:.2f}',
                                                              f'{a:.2f} to {b:.2f}'), chunk_frames))
                        result = {'answer': {'events': [event for item in smaller
                                                       for event in item['answer'].get('events', [])]},
                                  'seconds': sum(item['seconds'] for item in smaller),
                                  'usage': {'split_calls': [item['usage'] for item in smaller]},
                                  'split_reason': str(exc)[:200]}
                    mode_data['blocks'].append({'start': block_start, 'end': block_end,
                                                'frame_times': times, **result})
                    save(data)
                    print(scene, mode, f'block {index + 1}/{len(ranges)}',
                          f"{result['seconds']:.1f}s", flush=True)
                if 'summary' not in mode_data:
                    observations = [{'start': b['start'], 'end': b['end'],
                                     'events': b['answer'].get('events', [])}
                                    for b in mode_data['blocks']]
                    prompt = (f'Synthesize one chronology for {start:.2f}-{end:.2f}s from the observations. '
                              'Deduplicate overlap; preserve brief actions, scene changes and uncertainty. '
                              'Separate visible from spoken facts. Suggest two short hooks supported by the '
                              'chronology. Return JSON with events array (at, visible, spoken, uncertainty) '
                              'and hooks array. Observations: '
                              f'{json.dumps(observations, ensure_ascii=False)}')
                    mode_data['summary'] = ask(prompt, [])
                    save(data)
                    print(scene, mode, 'summary', flush=True)
    finally:
        capture.release()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--scene', choices=SCENES)
    parser.add_argument('--mode', choices=MODES)
    args = parser.parse_args()
    if args.scene:
        OUTPUT = OUTPUT.with_name(f'storyboard-fps-{args.scene}.json')
    run(args.scene, args.mode)
