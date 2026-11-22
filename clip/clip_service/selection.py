import re

KEYWORDS = {'важно', 'главное', 'итог', 'почему', 'как', 'ошибка', 'секрет', 'результат',
            'important', 'because', 'how', 'why', 'result', 'mistake', 'secret'}


def manual_candidates(intervals: list[dict], duration: float, min_seconds: float, max_seconds: float) -> list[dict]:
    result = []
    for item in intervals:
        start = max(0.0, float(item['start']))
        end = min(duration, float(item['end']))
        length = end - start
        if length <= 0 or length > max_seconds or (length < min_seconds and duration >= min_seconds):
            raise ValueError(f'invalid manual interval {start}-{end}')
        result.append({'start': round(start, 3), 'end': round(end, 3), 'score': 0.0,
                       'confidence': 0.0, 'algorithm': 'manual-v1'})
    return result


def select_candidates(segments: list[dict], duration: float, count: int,
                      min_seconds: float, max_seconds: float, loudness=None) -> list[dict]:
    if not segments:
        return []
    candidates = []
    for left in range(len(segments)):
        start = float(segments[left]['start'])
        texts, confidences = [], []
        for right in range(left, len(segments)):
            end = min(duration, float(segments[right]['end']))
            if end - start > max_seconds:
                break
            texts.append(segments[right].get('text', ''))
            confidences.append(float(segments[right].get('confidence', .5)))
            if end - start < min_seconds and duration >= min_seconds:
                continue
            text = ' '.join(texts).strip()
            words = {w.lower() for w in re.findall(r'[\w-]+', text)}
            keyword_score = min(.25, .05 * len(words & KEYWORDS))
            phrase_score = .12 if text.endswith(('.', '!', '?', '…')) else 0
            pause = (float(segments[right + 1]['start']) - end) if right + 1 < len(segments) else 1.0
            pause_score = min(.13, max(0, pause) * .08)
            confidence = sum(confidences) / len(confidences)
            density = min(1.0, len(words) / max(1.0, (end - start) * 1.5))
            score = .5 * confidence + .2 * density + keyword_score + phrase_score + pause_score
            candidates.append({'start': round(start, 3), 'end': round(end, 3),
                               'score': round(min(1.0, score), 3),
                               'confidence': round(confidence, 3), 'algorithm': 'transcript-v2'})
    ranked = sorted(candidates, key=lambda c: (-c['score'], c['start'], c['end']))[:max(count * 3, count)]
    if loudness:
        for candidate in ranked:
            db = loudness(candidate['start'], candidate['end'])
            candidate['loudness_db'] = round(db, 2)
            candidate['score'] = round(min(1.0, candidate['score'] + .1 * max(0, min(1, (db + 40) / 30))), 3)
    selected = []
    for candidate in sorted(ranked, key=lambda c: (-c['score'], c['start'], c['end'])):
        if all(min(candidate['end'], old['end']) - max(candidate['start'], old['start']) <= 0 for old in selected):
            selected.append(candidate)
        if len(selected) >= count:
            break
    return sorted(selected, key=lambda c: c['start'])


def clip_segments(segments: list[dict], start: float, end: float) -> list[dict]:
    result = []
    for segment in segments:
        seg_start, seg_end = float(segment['start']), float(segment['end'])
        if seg_end <= start or seg_start >= end:
            continue
        result.append({'start': max(0, seg_start - start), 'end': min(end, seg_end) - start,
                       'text': segment.get('text', '')})
    return result
