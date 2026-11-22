from clip_service.core import deterministic_candidates, naming, stable_id, subtitles


def test_stable_and_naming():
    assert stable_id('a') == stable_id('a')
    assert naming('job', 'clip') == 'job_clip.mp4'

def test_subtitles():
    text = subtitles([{'start': 1.2, 'end': 2.3, 'text': 'Привет'}])
    assert '00:00:01,200 --> 00:00:02,300' in text
    assert subtitles([], 'vtt').startswith('WEBVTT')

def test_candidates_bounds():
    values = deterministic_candidates(60, 3, 15, 30)
    assert len(values) == 3 and all(15 <= x['end'] - x['start'] <= 30 for x in values)
