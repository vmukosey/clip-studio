"""Burn-only ASS captions for gaming clips."""
import colorsys
import re
from pathlib import Path

import pysubs2


FONT_DIR = Path(__file__).resolve().parent
STOPWORDS = set(('the a an and or but to of in on at for with is are was were it this that '
                 'i you he she we they my your our me him her them can could would should '
                 'do did does have has had be been so just like yeah uh um oh hey okay ok '
                 'gonna gotta really very much fucking fuck '
                 'и а но в на к с по за из от для что это как когда где я ты он она мы вы они '
                 'мой твой наш мне тебе ему ей их уже еще ну вот да нет ага просто типа').split())


def _luma(rgb):
    values = [v / 255 for v in rgb]
    linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in values]
    return sum(a * b for a, b in zip(linear, (.2126, .7152, .0722)))


def accent_colors(source: Path, start: float, end: float) -> tuple[tuple[int, int, int], ...]:
    """Choose two fixed, vivid colors after sampling the scene and caption region."""
    import cv2
    import numpy as np

    capture = cv2.VideoCapture(str(source))
    hues, backgrounds = [], []
    try:
        for fraction in (.12, .5, .88):
            capture.set(cv2.CAP_PROP_POS_MSEC, (start + (end - start) * fraction) * 1000)
            ok, frame = capture.read()
            if not ok:
                continue
            frame = cv2.resize(frame, (160, 90))
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            colorful = hsv[(hsv[:, :, 1] > 65) & (hsv[:, :, 2] > 40)]
            if len(colorful):
                hues.extend(colorful[::max(1, len(colorful) // 600), 0].tolist())
            backgrounds.extend(cv2.cvtColor(frame[68:88], cv2.COLOR_BGR2RGB).reshape(-1, 3)[::20].tolist())
    finally:
        capture.release()
    if hues:
        angles = np.asarray(hues) * np.pi / 90
        dominant = (np.arctan2(np.sin(angles).mean(), np.cos(angles).mean()) / (2 * np.pi)) % 1
    else:
        dominant = .58
    background = np.median(backgrounds, axis=0) if backgrounds else (35, 35, 35)
    background_luma = _luma(background)
    candidates = []
    for offset in (.5, .38, .62, .28, .72):
        hue = (dominant + offset) % 1
        for lightness in (.67, .48):
            rgb = tuple(round(v * 255) for v in colorsys.hls_to_rgb(hue, lightness, .94))
            lum = _luma(rgb)
            contrast = (max(lum, background_luma) + .05) / (min(lum, background_luma) + .05)
            candidates.append((contrast, rgb))
    candidates.sort(reverse=True)
    first = candidates[0][1]
    second = next(rgb for _, rgb in candidates[1:]
                  if sum(abs(a - b) for a, b in zip(first, rgb)) > 160)
    return first, second


def _ass_color(rgb):
    return f'&H{rgb[2]:02X}{rgb[1]:02X}{rgb[0]:02X}&'


def _caption_text(text: str, colors) -> str:
    words = re.findall(r'\S+', text.replace('\n', ' '))
    eligible = [(i, word) for i, word in enumerate(words)
                if (re.fullmatch(r"[A-Za-zА-Яа-яЁё]+[!?.,]?", word)
                    and len(word.strip('!?.,')) >= 4
                    and word.strip('!?.,').casefold() not in STOPWORDS)]
    ranked = sorted(eligible, key=lambda pair: (-len(pair[1]), pair[0]))
    distinct = []
    seen = set()
    for index, word in ranked:
        key = word.strip('!?.,').casefold()
        if key not in seen:
            distinct.append(index)
            seen.add(key)
        if len(distinct) == 2:
            break
    chosen = {index: colors[n] for n, index in enumerate(distinct)} if len(words) >= 4 else {}
    lines, current = [], []
    for i, word in enumerate(words):
        candidate = ' '.join(current + [word])
        if current and len(candidate) > 28 and len(lines) == 0:
            lines.append(' '.join(current))
            current = []
        current.append(word)
    lines.append(' '.join(current))
    if (len(lines) > 2 or len(lines[-1]) > 36) and len(words) > 1:
        # Keep at most two lines while preserving the start of an overlong utterance.
        words = words[:max(1, len(words) - 1)]
        return _caption_text(' '.join(words), colors)
    rendered = []
    index = 0
    for line in lines:
        parts = []
        for word in line.split():
            escaped = word.replace('\\', '\\\\').replace('{', r'\{').replace('}', r'\}')
            parts.append((f'{{\\c{_ass_color(chosen[index])}}}{escaped}{{\\c&HFFFFFF&}}'
                          if index in chosen else escaped))
            index += 1
        rendered.append(' '.join(parts))
    return r'\N'.join(rendered)


def write_gaming_ass(srt_path: Path, ass_path: Path, colors) -> None:
    subs = pysubs2.load(str(srt_path), encoding='utf-8')
    subs.info['PlayResX'] = '1080'
    subs.info['PlayResY'] = '1920'
    style = subs.styles['Default']
    style.fontname = 'Rubik'
    style.fontsize = 77
    style.bold = True
    style.alignment = pysubs2.Alignment.BOTTOM_CENTER
    style.marginl = style.marginr = 65
    style.marginv = 178
    style.outline = 5
    style.shadow = 2
    style.primarycolor = pysubs2.Color(255, 255, 255)
    style.outlinecolor = pysubs2.Color(17, 20, 25)
    style.backcolor = pysubs2.Color(0, 0, 0, 0)
    for event in subs.events:
        event.text = _caption_text(event.plaintext, colors)
    subs.save(str(ass_path))
