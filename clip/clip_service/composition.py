import re
import json
import shutil
import statistics
import tempfile
from pathlib import Path
from uuid import uuid4

from .config import settings
from .core import stable_id, write_atomic
from .gpu import resolve_acceleration
from .gaming_captions import FONT_DIR, accent_colors, write_gaming_ass
from .media import MediaError, probe_video, require_free_space, run


def _tracked_center(path: Path, start: float, end: float, width: int) -> float:
    if not settings.clip_face_tracking:
        return width / 2
    try:
        import cv2
    except ImportError:
        return width / 2
    capture = cv2.VideoCapture(str(path))
    cascade_path = Path(cv2.data.haarcascades) / 'haarcascade_frontalface_default.xml'
    if not cascade_path.is_file():
        capture.release()
        return width / 2
    with tempfile.TemporaryDirectory(prefix='clip-haar-') as directory:
        ascii_path = Path(directory) / 'haarcascade.xml'
        shutil.copyfile(cascade_path, ascii_path)
        detector = cv2.CascadeClassifier(str(ascii_path))
    if detector.empty():
        capture.release()
        return width / 2
    centers = []
    moment = start
    try:
        while moment < end:
            capture.set(cv2.CAP_PROP_POS_MSEC, moment * 1000)
            ok, frame = capture.read()
            if not ok:
                break
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5,
                                               minSize=(max(24, width // 20),) * 2)
            if len(faces):
                x, _, w, _ = max(faces, key=lambda face: face[2] * face[3])
                centers.append(float(x + w / 2))
            moment += max(.25, settings.clip_face_sample_seconds)
    finally:
        capture.release()
    return statistics.median(centers) if centers else width / 2


RENDER_VERSION = 'gaming-tall-ass-v13'


def _confirmed_facecam(layout: dict, plans: list[dict]) -> dict | None:
    if not layout.get('present') or layout.get('side') == 'none':
        return None
    boxes = [plan['facecam'] for plan in plans if isinstance(plan, dict) and plan.get('facecam')]
    if len(boxes) < 2 or len(boxes) * 2 < len(plans):
        return None
    side = layout.get('side')
    if side not in {'left', 'right'}:
        side = 'left' if statistics.median(b['x'] + b['w'] / 2 for b in boxes) < .5 else 'right'
    boxes = [b for b in boxes if ('left' if b['x'] + b['w'] / 2 < .5 else 'right') == side]
    if len(boxes) < 2:
        return None
    return {key: statistics.median(b[key] for b in boxes) for key in ('x', 'y', 'w', 'h')}


def _gaming_filter(width: int, height: int, center: float, layout: dict, plans: list[dict],
                   prefix: str, source_height: int) -> tuple[str, str, bool]:
    face = _confirmed_facecam(layout, plans)
    game_h = 1155 if face else 1210
    game_w = 1080
    game_y = 125 if face else 155
    # Crop at the destination aspect ratio without stretching the source.
    ratio = game_w / game_h
    crop_w = min(width, int(height * ratio))
    if face:
        face_mid = face['x'] + face['w'] / 2
        available = (width * (1 - face['x'] - face['w']) if face_mid < .5
                     else width * face['x'])
        crop_w = min(crop_w, int(available))
    crop_h = min(height, int(crop_w / ratio))
    crop_w = max(2, crop_w // 2 * 2)
    crop_h = max(2, crop_h // 2 * 2)
    # Leave a little more of the left HUD visible when there is no camera.
    left_bias = int(width * .08) if not face else 0
    crop_x = max(0, min(width - crop_w, int(center - crop_w / 2 - left_bias)))
    if face:
        crop_x = (max(crop_x, int((face['x'] + face['w']) * width)) if face_mid < .5
                  else min(crop_x, int(face['x'] * width) - crop_w))
    action_ys = [p['action']['y'] + p['action']['h'] / 2 for p in plans if p.get('action')]
    action_y = statistics.median(action_ys) if action_ys else .5
    crop_y = max(0, min(height - crop_h, int(action_y * height - crop_h / 2)))
    branches = [f'{prefix}split={3 if face else 2}' + ('[bg][game][cam]' if face else '[bg][game]'),
                '[bg]scale=1080:1920:force_original_aspect_ratio=increase:flags=bicubic,'
                'crop=1080:1920,boxblur=34:10[back]',
                f'[game]crop={crop_w}:{crop_h}:{crop_x}:{crop_y},scale={game_w}:{game_h}:flags=lanczos[sharp]',
                f'[back][sharp]overlay=0:{game_y}' + ('[composed]' if face else ',setsar=1')]
    if face:
        cam_x = max(0, min(width - 2, int(face['x'] * width)))
        cam_y = max(0, min(height - 2, int(face['y'] * source_height)))
        cam_w = max(2, min(width - cam_x, int(face['w'] * width)))
        cam_h = max(2, min(height - cam_y, int(face['h'] * source_height)))
        out_w = min(583, int(cam_w * 341 / cam_h))
        out_w = max(2, out_w // 2 * 2)
        out_h = min(341, int(cam_h * out_w / cam_w)) // 2 * 2
        blogger_y = game_y + game_h - 110
        feather = max(12, int(min(out_w, out_h) * .12))
        branches += [f'[cam]crop={cam_w}:{cam_h}:{cam_x}:{cam_y},scale={out_w}:{out_h}:flags=lanczos,'
                     'format=rgba,drawbox=x=2:y=2:w=iw-4:h=ih-4:color=white@0.7:t=2,'
                     f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='255*clip(min(min(X,W-1-X),min(Y,H-1-Y))/{feather},0,1)'[blogger]",
                     '[blogger]split[face][shadowbase]',
                     '[shadowbase]colorchannelmixer=rr=0:gg=0:bb=0:aa=0.35,boxblur=12:1[shadow]',
                     f'[composed][shadow]overlay={1080 - out_w}:{blogger_y + 8}[shadowed]',
                     f'[shadowed][face]overlay={1080 - out_w}:{blogger_y},setsar=1']
    return ';'.join(branches), 'gaming-camera' if face else 'gaming', bool(face)


def crop_filter(path: Path, probe: dict, start: float, end: float, layout: dict | None = None) -> tuple[str, str]:
    width, height = probe['width'], probe['height']
    source_height = height
    target_ratio = 9 / 16
    plans = (layout or {}).get('plans') or []
    points = (layout or {}).get('crop_points') if layout else None
    if plans and not points:
        points = [{'x': plan['action']['x'] + plan['action']['w'] / 2}
                  for plan in plans if plan.get('action')]
    if not isinstance(points, list) or not all(
            isinstance(point, dict) and isinstance(point.get('x'), (int, float))
            and 0 <= point['x'] <= 1 for point in points):
        points = None
    if points:
        xs = [float(point.get('x', .5)) for point in points if isinstance(point, dict)]
        center = statistics.median(xs) * width if xs else _tracked_center(path, start, end, width)
    else:
        center = _tracked_center(path, start, end, width)
    text_box = next((plan['embedded_text'] for plan in plans if plan.get('embedded_text')), None)
    lower_text = [plan['embedded_text'] for plan in plans if plan.get('embedded_text')
                  and plan['embedded_text']['y'] >= .82]
    if layout and layout.get('gaming') and len(lower_text) >= 2:
        text_box = {**lower_text[0], 'y': statistics.median(box['y'] for box in lower_text)}
    prefix = ''
    method = (layout or {}).get('text_method', 'auto')
    if text_box and method != 'none':
        x = int(text_box['x'] * width)
        y = int(text_box['y'] * height)
        w = max(2, int(text_box['w'] * width))
        h = max(2, int(text_box['h'] * height))
        action_overlap = any(p.get('action') and p['action']['y'] < text_box['y'] + text_box['h']
                             and p['action']['y'] + p['action']['h'] > text_box['y'] for p in plans)
        if method == 'auto':
            method = ('crop' if (layout and layout.get('gaming') and len(lower_text) >= 2)
                      or y > height * .82 or (y > height * .72 and not action_overlap) else 'delogo')
        if method == 'crop':
            prefix = f'crop=iw:{max(2, y)}:0:0,'
            height = max(2, y)
        elif method == 'mask':
            prefix = f'drawbox=x={x}:y={y}:w={w}:h={h}:color=black@1:t=fill,'
        elif method == 'delogo':
            prefix = f'delogo=x={x}:y={y}:w={w}:h={h}:show=0,'
    if layout and layout.get('gaming'):
        graph, mode, _ = _gaming_filter(width, height, center, layout, plans, prefix, source_height)
        return graph, mode
    if width / height > target_ratio:
        crop_width = max(2, int(height * target_ratio) // 2 * 2)
        x = max(0, min(width - crop_width, int(center - crop_width / 2)))
        crop = f'crop={crop_width}:{height}:{x}:0'
        mode = 'face' if abs(center - width / 2) > 1 else 'center'
    else:
        crop_height = max(2, int(width / target_ratio) // 2 * 2)
        y = max(0, (height - crop_height) // 2)
        crop = f'crop={width}:{crop_height}:0:{y}'
        mode = 'center'
    return f'{prefix}{crop},scale=1080:1920:flags=lanczos,setsar=1', mode


def _drawtext_value(value: str) -> str:
    return str(value).replace('\\', '\\\\').replace("'", "\\'").replace(':', '\\:').replace('%', '\\%')


def _color(value: str, default: str) -> str:
    return value if re.fullmatch(r'(#[0-9a-fA-F]{6}|[a-zA-Z]+)(@[0-9.]+)?', str(value)) else default


def _ad_schedule(rules: dict, clip_duration: float) -> list[dict]:
    ads = rules.get('fullscreen', [])
    if not isinstance(ads, list):
        return []
    frequency = max(1.0, float(rules.get('fullscreen_frequency_seconds', 30)))
    cursor, result = 0.0, []
    for ad in sorted((a for a in ads if isinstance(a, dict)), key=lambda a: -int(a.get('priority', 0))):
        path = Path(str(ad.get('path', '')))
        duration = min(max(.1, float(ad.get('duration', 3))), clip_duration)
        start = max(cursor, float(ad.get('start', cursor)))
        if path.suffix.lower() == '.mp4' and path.is_file() and start + duration <= clip_duration:
            result.append({'path': path, 'start': start, 'end': start + duration})
            cursor = start + frequency
    return result


def video_encoder_args(video_encoder: str) -> list[str]:
    if video_encoder == 'h264_nvenc':
        return ['-c:v', 'h264_nvenc', '-preset', 'p5', '-tune', 'hq', '-rc', 'vbr',
                '-cq', '21', '-b:v', '0']
    if video_encoder == 'libx264':
        return ['-c:v', 'libx264', '-preset', 'medium', '-crf', '21']
    raise MediaError(f'unsupported video encoder: {video_encoder}')


def render_vertical(source: Path, probe: dict, start: float, end: float, subtitle_path: Path,
                    output: Path, thumbnail: Path, rules: dict | None = None,
                    acceleration: dict | None = None, layout: dict | None = None,
                    force: bool = False) -> dict:
    signature = stable_id(RENDER_VERSION, str(source.resolve()), str(source.stat().st_mtime_ns),
                          str(start), str(end), json.dumps(layout or {}, sort_keys=True),
                          json.dumps(rules or {}, sort_keys=True), subtitle_path.read_text(encoding='utf-8'))
    cache_path = output.with_suffix('.render-cache.json')
    if (not force and cache_path.is_file() and
            json.loads(cache_path.read_text(encoding='utf-8')).get('signature') == signature and
            output.exists() and output.stat().st_size and thumbnail.exists() and thumbnail.stat().st_size):
        existing = probe_video(output)
        if existing['width'] == 1080 and existing['height'] == 1920:
            return {'crop_mode': 'cached', 'ads': 0,
                    'video_encoder': (acceleration or resolve_acceleration())['video_encoder']}
    require_free_space(output.parent)
    rules = rules or {}
    base_filter, crop_mode = crop_filter(source, probe, start, end, layout)
    safe_srt = subtitle_path
    if layout and layout.get('gaming') and rules.get('burn_subtitles', True):
        safe_srt = output.parent / f'.subtitle-{uuid4().hex}.ass'
        write_gaming_ass(subtitle_path, safe_srt, accent_colors(source, start, end))
    elif "'" in str(subtitle_path):
        safe_srt = subtitle_path.with_name(f'.subtitle-{uuid4().hex}.srt')
        shutil.copyfile(subtitle_path, safe_srt)
    escaped_srt = str(safe_srt).replace('\\', '\\\\').replace(':', '\\:').replace("'", "\\'")
    if layout and layout.get('gaming'):
        style = ''
    else:
        style = f"FontName={settings.clip_subtitle_font},FontSize={settings.clip_subtitle_font_size},Alignment=2,MarginV=100,Outline=2,Shadow=1"
    filters = [base_filter]
    if rules.get('burn_subtitles', bool(layout and layout.get('gaming'))):
        if layout and layout.get('gaming'):
            fontsdir = str(FONT_DIR).replace('\\', '\\\\').replace(':', '\\:').replace("'", "\\'")
            filters.append(f"subtitles='{escaped_srt}':fontsdir='{fontsdir}'")
        else:
            filters.append(f"subtitles='{escaped_srt}':force_style='{style}'")
    banner = rules.get('banner', {}) if isinstance(rules.get('banner', {}), dict) else {}
    if banner.get('text'):
        height = max(40, min(400, int(banner.get('height', 150))))
        bg = _color(banner.get('background', 'black@0.75'), 'black@0.75')
        fg = _color(banner.get('color', 'white'), 'white')
        text = _drawtext_value(banner['text'])
        filters += [f'drawbox=x=0:y=ih-{height}:w=iw:h={height}:color={bg}:t=fill',
                    f"drawtext=text='{text}':fontcolor={fg}:fontsize=42:x=(w-text_w)/2:y=h-{height}+({height}-text_h)/2"]
    watermark = rules.get('watermark', {}) if isinstance(rules.get('watermark', {}), dict) else {}
    if watermark.get('text'):
        text = _drawtext_value(watermark['text'])
        filters.append(f"drawtext=text='{text}':fontcolor=white@0.65:fontsize=30:x=w-text_w-40:y=40")

    duration = end - start
    ads = _ad_schedule(rules, duration)
    command = ['ffmpeg', '-y', '-ss', str(start), '-t', str(duration), '-i', str(source)]
    for ad in ads:
        command += ['-i', str(ad['path'])]
    if ads:
        graph = [f"[0:v]{','.join(filters)}[base]"]
        current = 'base'
        for index, ad in enumerate(ads, 1):
            graph.append(f'[{index}:v]scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2[ad{index}]')
            out = f'v{index}'
            graph.append(f"[{current}][ad{index}]overlay=0:0:enable='between(t,{ad['start']},{ad['end']})'[{out}]")
            current = out
        command += ['-filter_complex', ';'.join(graph), '-map', f'[{current}]']
    elif ';' in ','.join(filters):
        graph = f"[0:v]{','.join(filters)}[vout]"
        command += ['-filter_complex', graph, '-map', '[vout]']
    else:
        command += ['-vf', ','.join(filters), '-map', '0:v:0']
    acceleration = acceleration or resolve_acceleration()
    command += ['-map', '0:a?'] + video_encoder_args(acceleration['video_encoder'])
    command += ['-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart']
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.stem + '.tmp' + output.suffix)
    command.append(str(temporary))
    try:
        run(command)
    finally:
        if safe_srt != subtitle_path:
            safe_srt.unlink(missing_ok=True)
    probe_video(temporary)
    temporary.replace(output)

    thumb_tmp = thumbnail.with_name(thumbnail.stem + '.tmp' + thumbnail.suffix)
    run(['ffmpeg', '-y', '-ss', str(min(.5, duration / 2)), '-i', str(output), '-frames:v', '1',
         '-q:v', '2', str(thumb_tmp)])
    if not thumb_tmp.exists() or not thumb_tmp.stat().st_size:
        raise MediaError('thumbnail was not created')
    thumb_tmp.replace(thumbnail)
    write_atomic(cache_path, json.dumps({'signature': signature}).encode('utf-8'))
    return {'crop_mode': crop_mode, 'ads': len(ads), 'video_encoder': acceleration['video_encoder']}
