import pytest

from clip_service import episodes
from clip_service.composition import crop_filter
from clip_service import transcription


def test_invalid_model_box_retries_and_does_not_cache(tmp_path, monkeypatch):
    calls = []
    def ask(*_args):
        calls.append(1)
        return {'suitable': True, 'start': 0, 'end': 8,
                'action': {'x': 1.4, 'y': 0, 'w': .2, 'h': .2}}
    monkeypatch.setattr(episodes, '_ask', ask)
    with pytest.raises(ValueError, match='three times'):
        episodes._cached(tmp_path, 'invalid', 'prompt', [],
                         lambda r: episodes._validate_plan(r, 0, 8))
    assert len(calls) == 3
    assert not (tmp_path / 'invalid.json').exists()


def test_short_event_and_camera_change_stay_together(tmp_path, monkeypatch):
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'x')
    monkeypatch.setattr(episodes, '_samples', lambda *_args: [(i, .2, None) for i in range(20)])
    calls = []
    def cached(_dir, key, _prompt, _frames, _validator):
        calls.append(key)
        if key.startswith('window'):
            return {'events': [{'start': 2, 'end': 10, 'title': 'Interview', 'description': 'One answer'}]}
        return {'suitable': True, 'start': 2, 'end': 10, 'action': None,
                'facecam': None, 'embedded_text': None, 'reason': ''}
    monkeypatch.setattr(episodes, '_cached', cached)
    clips = episodes.discover(source, [], 20, None, 90)
    assert [(c['start'], c['end']) for c in clips] == [(2, 10)]
    assert len(calls) == 4


def test_text_removal_variants_preserve_configured_focus(tmp_path):
    plan = {'at': 4, 'action': {'x': .7, 'y': .2, 'w': .2, 'h': .3},
            'facecam': None, 'embedded_text': {'x': .1, 'y': .8, 'w': .3, 'h': .1}}
    source = tmp_path / 'source.mp4'
    probe = {'width': 1920, 'height': 1080}
    for method, expected in [('crop', 'crop=iw:'), ('mask', 'drawbox='),
                             ('delogo', 'delogo=')]:
        filt, _ = crop_filter(source, probe, 0, 8, {'plans': [plan], 'text_method': method})
        assert expected in filt
        assert 'scale=1080:1920' in filt


def test_per_clip_transcript_is_cached_with_local_timestamps(tmp_path, monkeypatch):
    calls = []
    def run(command):
        calls.append(command)
    class Segment:
        start, end, text = .5, 2.5, ' Come here! '
    class Model:
        def transcribe(self, *_args, **_kwargs):
            return iter([Segment()]), type('Info', (), {'language': 'en'})()
    monkeypatch.setattr(transcription, 'run', run)
    monkeypatch.setattr(transcription, '_model', lambda *_args: Model())
    acceleration = {'whisper_device': 'cpu', 'whisper_compute_type': 'int8'}
    cache = tmp_path / 'clip.json'
    first = transcription.transcribe_clip(tmp_path / 'source.mp4', 10, 14, 'en', cache, acceleration)
    second = transcription.transcribe_clip(tmp_path / 'source.mp4', 10, 14, 'en', cache, acceleration)
    assert first == second == [{'start': .5, 'end': 2.5, 'text': 'Come here!'}]
    assert len(calls) == 1
