import json
import re
from pathlib import Path
from uuid import uuid4

import pytest

from clip_service import service
from clip_service.composition import RENDER_VERSION, _ad_schedule, crop_filter, video_encoder_args
from clip_service.gaming_captions import _caption_text, accent_colors, write_gaming_ass
from clip_service.core import canonical_json
from clip_service.db import Base, Clip, Job, SessionLocal, Source, engine
from clip_service.selection import clip_segments, manual_candidates, select_candidates
from clip_service.editorial import judge, recommend


def test_canonical_json_and_manual_intervals():
    assert canonical_json({'b': 1, 'a': {'d': 2, 'c': 3}}) == canonical_json({'a': {'c': 3, 'd': 2}, 'b': 1})
    values = manual_candidates([{'start': 5, 'end': 20}], 30, 15, 90)
    assert values[0]['algorithm'] == 'manual-v1'
    with pytest.raises(ValueError):
        manual_candidates([{'start': 20, 'end': 5}], 30, 15, 90)


def test_transcript_selection_uses_phrase_pause_and_loudness():
    segments = [
        {'start': 0, 'end': 8, 'text': 'Почему это важно', 'confidence': .9},
        {'start': 8, 'end': 16, 'text': 'Вот главный результат.', 'confidence': .9},
        {'start': 20, 'end': 36, 'text': 'обычный следующий фрагмент.', 'confidence': .6},
    ]
    selected = select_candidates(segments, 40, 1, 15, 30, lambda _start, _end: -10)
    assert selected[0]['start'] == 0
    assert selected[0]['loudness_db'] == -10
    local = clip_segments(segments, 5, 18)
    assert local[0]['start'] == 0 and local[-1]['end'] == 11


def test_center_crop_and_ad_rules(tmp_path, monkeypatch):
    monkeypatch.setattr('clip_service.composition._tracked_center', lambda *_args: 1600)
    value, mode = crop_filter(tmp_path / 'source.mp4', {'width': 1920, 'height': 1080}, 0, 20)
    assert value.startswith('crop=606:1080:') and value.endswith('scale=1080:1920:flags=lanczos,setsar=1')
    assert mode == 'face'
    malformed, _ = crop_filter(tmp_path / 'source.mp4', {'width': 1920, 'height': 1080},
                               0, 20, {'crop_points': [{'x': 960, 'y': 540}]})
    assert malformed == value
    ad = tmp_path / 'ad.mp4'; ad.write_bytes(b'x')
    schedule = _ad_schedule({'fullscreen': [{'path': str(ad), 'duration': 2, 'priority': 10}]}, 10)
    assert schedule[0]['end'] == 2


@pytest.mark.parametrize(('boxes', 'side', 'camera'), [
    ([], None, False),
    ([{'x': .02, 'y': .05, 'w': .23, 'h': .25}] * 3, 'left', True),
    ([{'x': .75, 'y': .05, 'w': .23, 'h': .25}] * 3, 'right', True),
    ([{'x': .02, 'y': .05, 'w': .23, 'h': .25}], 'left', False),
])
def test_gaming_frame_uses_only_confirmed_facecam(tmp_path, monkeypatch, boxes, side, camera):
    monkeypatch.setattr('clip_service.composition._tracked_center', lambda *_args: 960)
    plans = [{'action': {'x': .35, 'y': .3, 'w': .2, 'h': .4},
              'facecam': boxes[i] if i < len(boxes) else None} for i in range(3)]
    graph, mode = crop_filter(tmp_path / 'source.mp4', {'width': 1920, 'height': 1080},
                              0, 22, {'gaming': True, 'present': bool(boxes),
                                      'side': side, 'plans': plans})
    assert 'boxblur=34:10' in graph
    assert f'scale=1080:{1155 if camera else 1210}:flags=lanczos' in graph
    assert f'overlay=0:{125 if camera else 155}' in graph
    assert ('[blogger]' in graph) is camera
    assert mode == ('gaming-camera' if camera else 'gaming')
    crop = re.search(r'\[game\]crop=(\d+):(\d+):(\d+):(\d+)', graph)
    assert crop is not None
    crop_width, crop_height, crop_x = int(crop[1]), int(crop[2]), int(crop[3])
    assert abs(crop_width / crop_height - 1080 / (1155 if camera else 1210)) < .003
    if camera:
        blogger = re.search(r'\[cam\]crop=(\d+):(\d+):\d+:\d+,scale=(\d+):(\d+)', graph)
        assert blogger is not None
        cam_w, cam_h, out_w, out_h = map(int, blogger.groups())
        assert abs(cam_w / cam_h - out_w / out_h) < .015
        assert out_w <= 583 and out_h <= 341
        assert f'[shadowed][face]overlay={1080 - out_w}:1170' in graph
        assert f'[composed][shadow]overlay={1080 - out_w}:1178' in graph
        assert 1178 + out_h < 1560
        assert 'geq=' in graph and 'drawbox=' in graph
        if side == 'left':
            assert crop_x >= int((boxes[0]['x'] + boxes[0]['w']) * 1920)
        else:
            assert crop_x + crop_width <= int(boxes[0]['x'] * 1920)
    else:
        assert 0 < crop_x < 1920 - crop_width


def test_gaming_left_hud_stays_in_frame_with_single_camera_mark(tmp_path):
    plans = [{'action': {'x': x, 'y': .37, 'w': w, 'h': .63},
              'facecam': {'x': 0, 'y': .078, 'w': .263, 'h': .267} if i == 2 else None}
             for i, (x, w) in enumerate(((.25, .25), (.23, .27), (.35, .16)))]
    graph, mode = crop_filter(tmp_path / 'source.mp4', {'width': 1920, 'height': 1080},
                              0, 22, {'gaming': True, 'present': True, 'plans': plans})
    assert mode == 'gaming'
    assert '[game]crop=962:1078:86:' in graph
    assert '[blogger]' not in graph


def test_camera_crop_uses_original_height_after_game_text_removal(tmp_path):
    facecam = {'x': .02, 'y': .04, 'w': .25, 'h': .25}
    plans = [{'action': {'x': .35, 'y': .3, 'w': .2, 'h': .4},
              'facecam': facecam,
              'embedded_text': {'x': .3, 'y': .88, 'w': .4, 'h': .08}} for _ in range(3)]
    graph, mode = crop_filter(tmp_path / 'source.mp4', {'width': 1920, 'height': 1080},
                              0, 9, {'gaming': True, 'present': True,
                                     'side': 'left', 'plans': plans})
    assert mode == 'gaming-camera'
    assert 'crop=iw:950:0:0,split=3' in graph
    assert '[cam]crop=480:270:38:43' in graph


def test_gaming_ass_captions_are_two_lines_with_selective_accents(tmp_path):
    import pysubs2

    colors = ((255, 90, 40), (50, 230, 200))
    text = _caption_text('Почему этот невероятный момент выглядит настолько круто сегодня', colors)
    assert text.count(r'\N') <= 1
    assert text.count(r'{\c&HFFFFFF&}') == 2
    for phrase in ('Это действительно невероятное приключение продолжается прямо сейчас',
                   'This incredible moment looks amazing and keeps getting better today'):
        wrapped = _caption_text(phrase, colors)
        assert wrapped.count(r'\N') == 1
        assert all(len(re.sub(r'{[^}]*}', '', line)) <= 36 for line in wrapped.split(r'\N'))
    assert _caption_text('Ну да', colors) == 'Ну да'
    srt = tmp_path / 'source.srt'
    srt.write_text('1\n00:00:00,000 --> 00:00:02,000\nThis incredible moment looks amazing today.\n', encoding='utf-8')
    ass = tmp_path / 'burn.ass'
    write_gaming_ass(srt, ass, colors)
    parsed = pysubs2.load(str(ass))
    assert parsed.styles['Default'].fontname == 'Rubik'
    assert parsed.styles['Default'].fontsize == 77
    assert parsed.styles['Default'].outline == 5
    assert parsed.styles['Default'].marginv == 178
    assert parsed.info['PlayResY'] == '1920'
    assert RENDER_VERSION == 'gaming-tall-ass-v13'


def test_gaming_accents_are_fixed_and_distinct(tmp_path):
    import cv2
    import numpy as np

    source = tmp_path / 'scene.avi'
    writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*'MJPG'), 2, (160, 90))
    for _ in range(6):
        writer.write(np.full((90, 160, 3), (180, 70, 35), dtype=np.uint8))
    writer.release()
    first = accent_colors(source, 0, 3)
    assert first == accent_colors(source, 0, 3)
    assert len(first) == 2
    assert sum(abs(a - b) for a, b in zip(*first)) > 160


def test_process_job_waits_for_review_and_marks_dead_letter(tmp_path, monkeypatch):
    Base.metadata.create_all(engine)
    source_path = tmp_path / 'input.mp4'; source_path.write_bytes(b'input')
    source_id, job_id = 'pipeline-source', 'pipeline-job'
    with SessionLocal() as db:
        db.query(Clip).filter(Clip.job_id == job_id).delete()
        db.query(Job).filter(Job.id == job_id).delete()
        db.query(Source).filter(Source.id == source_id).delete()
        db.add(Source(id=source_id, kind='local', uri=str(source_path)))
        db.add(Job(id=job_id, source_id=source_id, config={'intervals': [{'start': 0, 'end': 10}]}))
        db.commit()
    monkeypatch.setattr(service.settings, 'clip_storage_root', tmp_path)
    monkeypatch.setattr(service.settings, 'clip_min_seconds', 15)
    monkeypatch.setattr(service, 'prepare_source', lambda _source: (source_path, {'duration': 10, 'width': 1920, 'height': 1080, 'has_audio': False}))
    first = service.process_job(job_id)
    second = service.process_job(job_id)
    assert first['status'] == second['status'] == 'awaiting_review'
    assert first['clips'][0]['id'] == second['clips'][0]['id']
    assert first['clips'][0]['artifacts'] == {}
    assert service.review_clip(first['clips'][0]['id'], 'reject', 'weak hook')['status'] == 'rejected'
    with pytest.raises(ValueError, match='approved'):
        service.export_clip(first['clips'][0]['id'])
    with pytest.raises(ValueError, match='approved'):
        service.finalize_cutwright(first['clips'][0]['id'], str(source_path))
    assert service.decisions(first['clips'][0]['id'])[0]['reason'] == 'weak hook'

    with SessionLocal() as db:
        job = db.get(Job, job_id); job.status = 'queued'; job.attempts = service.settings.clip_max_attempts - 1
        db.commit()
    monkeypatch.setattr(service, 'prepare_source', lambda _source: (_ for _ in ()).throw(RuntimeError('forced')))
    with pytest.raises(RuntimeError):
        service.process_job(job_id)
    assert service.job_view(job_id)['status'] == 'dead_letter'


def test_zero_candidates_and_manual_addition(tmp_path, monkeypatch):
    Base.metadata.create_all(engine)
    path = tmp_path / 'input.mp4'; path.write_bytes(b'input')
    source_id, job_id = uuid4().hex, uuid4().hex
    with SessionLocal() as db:
        db.add(Source(id=source_id, kind='local', uri=str(path)))
        db.add(Job(id=job_id, source_id=source_id, config={'intervals': []}))
        db.commit()
    monkeypatch.setattr(service.settings, 'clip_storage_root', tmp_path)
    monkeypatch.setattr(service, 'prepare_source', lambda _source: (path, {'duration': 50, 'width': 1920, 'height': 1080, 'has_audio': False}))
    result = service.process_job(job_id)
    assert result['status'] == 'no_candidates' and not result['clips']
    added = service.add_interval(job_id, 10, 30)
    assert service.review_clip(added['id'], 'approve')['status'] == 'approved'
    assert service.job_view(job_id)['clips'][0]['status'] == 'approved'


def test_auto_discovers_source_order_and_renders_before_selection(tmp_path, monkeypatch):
    Base.metadata.create_all(engine)
    path = tmp_path / 'input.mp4'; path.write_bytes(b'input')
    source_id, job_id = uuid4().hex, uuid4().hex
    with SessionLocal() as db:
        db.add(Source(id=source_id, kind='local', uri=str(path)))
        db.add(Job(id=job_id, source_id=source_id, config={'template': 'gaming', 'count': None}))
        db.commit()
    monkeypatch.setattr(service.settings, 'clip_storage_root', tmp_path)
    monkeypatch.setattr(service.settings, 'clip_perlapenna_base_url', 'http://vision.test/v1')
    monkeypatch.setattr(service.settings, 'clip_perlapenna_api_key', 'test-key')
    monkeypatch.setattr(service, 'prepare_source', lambda _source: (path, {
        'duration': 600, 'width': 1920, 'height': 1080, 'has_audio': True}))
    monkeypatch.setattr(service, 'transcribe', lambda *_args: [])
    observed = []
    def discover(_path, _segments, _duration, count, _max_seconds):
        observed.append(count)
        return [{'start': i * 40, 'end': i * 40 + 7, 'score': 1,
                 'confidence': 1, 'algorithm': 'storyboard-v1'} for i in range(12)]
    monkeypatch.setattr(service, 'discover', discover)
    rendered = []
    def export(clip_id):
        rendered.append(clip_id)
        with SessionLocal() as db:
            clip = db.get(Clip, clip_id)
            clip.status = 'exported'
            db.commit()
    monkeypatch.setattr(service, 'export_clip', export)
    result = service.process_job(job_id)
    assert observed == [None]
    assert result['status'] == 'exported'
    assert len(result['clips']) == len(rendered) == 12
    assert [c['start'] for c in result['clips']] == list(range(0, 480, 40))
    assert result['clips'][0]['selection'] == 'unselected'
    assert service.choose_clip(result['clips'][0]['id'], 'keep')['selection'] == 'keep'
    assert service.job_view(job_id)['clips'][0]['status'] == 'exported'


def test_editorial_rejection_and_no_quota_filling(monkeypatch):
    segments = [
        {'start': 0, 'end': 8, 'text': 'Почему это важно для здоровья человека?'},
        {'start': 8, 'end': 20, 'text': 'Есть конкретный результат и ясный вывод.'},
        {'start': 20, 'end': 30, 'text': 'Ставьте лайки и подписывайтесь на канал.'},
    ]
    monkeypatch.setattr('clip_service.editorial.judge', lambda _text: {
        'approved': False, 'score': 3, 'reason': 'no self-contained conclusion'})
    assert recommend(segments, 30, 5, 15, 90) == []


def test_llm_score_is_editorial_gate(monkeypatch):
    monkeypatch.setattr('clip_service.editorial.settings.clip_llm_base_url', 'http://localhost/v1')
    monkeypatch.setattr('clip_service.editorial.settings.clip_llm_api_key', 'test-key')
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def read(self, *_args):
            return json.dumps({'choices': [{'message': {'content': json.dumps({
                'approved': True, 'score': 7, 'reason': 'weak conclusion'})}}]}).encode()
    def fake_open(request, timeout):
        assert request.full_url == 'http://localhost/v1/chat/completions'
        assert json.loads(request.data)['model'] == 'gpt-6-luna'
        return Response()
    monkeypatch.setattr('clip_service.editorial.urlopen', fake_open)
    assert judge('A weak thought')['approved'] is False


def test_gpu_selection_strict_mode_and_nvenc_arguments(monkeypatch):
    from clip_service.gpu import AcceleratorError, resolve_acceleration

    monkeypatch.setattr(service.settings, 'clip_whisper_device', 'auto')
    monkeypatch.setattr(service.settings, 'clip_whisper_compute_type', 'auto')
    monkeypatch.setattr(service.settings, 'clip_video_encoder', 'auto')
    monkeypatch.setattr(service.settings, 'clip_require_gpu', False)
    gpu = resolve_acceleration({'cuda': True, 'cuda_device_count': 1, 'cuda_error': '',
                                'nvenc': True, 'nvenc_error': '', 'ffmpeg': True})
    assert gpu['whisper_device'] == 'cuda'
    assert gpu['whisper_compute_type'] == 'int8_float16'
    assert gpu['video_encoder'] == 'h264_nvenc'
    assert video_encoder_args('h264_nvenc') == ['-c:v', 'h264_nvenc', '-preset', 'p5', '-tune', 'hq',
                                                  '-rc', 'vbr', '-cq', '21', '-b:v', '0']

    monkeypatch.setattr(service.settings, 'clip_require_gpu', True)
    with pytest.raises(AcceleratorError, match='CLIP_REQUIRE_GPU'):
        resolve_acceleration({'cuda': False, 'cuda_device_count': 0, 'cuda_error': '',
                              'nvenc': False, 'nvenc_error': '', 'ffmpeg': True})
