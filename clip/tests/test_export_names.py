import json
from pathlib import Path
from uuid import uuid4

from clip_service import service
from clip_service.db import Base, Clip, Job, SessionLocal, Source, engine
from clip_service.export_names import available_base, export_dir, safe_name


def test_export_paths_and_repeated_runs(tmp_path, monkeypatch):
    Base.metadata.create_all(engine)
    monkeypatch.setattr(service.settings, 'clip_storage_root', tmp_path)
    source = service.create_source(str(tmp_path / 'same.mp4'))
    other = service.create_source(str(tmp_path / 'other' / 'same.mp4'))
    first = service.create_job(source.id, {'template': 'gaming'})
    second = service.create_job(source.id, {'template': 'gaming'})
    third = service.create_job(other.id, {'template': 'gaming'})
    assert len({first.id, second.id, third.id}) == 3
    assert export_dir(source, first).parent == export_dir(source, second).parent
    assert export_dir(source, first).parent != export_dir(other, third).parent
    assert safe_name('CON') == 'Untitled'
    assert safe_name('Shark: <attack>?') == 'Shark attack'
    directory = export_dir(source, first)
    directory.mkdir(parents=True)
    (directory / '01 - Shark attack.mp4').write_bytes(b'x')
    assert available_base(directory, 1, 'Shark: <attack>?') == '01 - Shark attack (2)'


def test_finalize_and_rename_bundle(tmp_path, monkeypatch):
    Base.metadata.create_all(engine)
    monkeypatch.setattr(service.settings, 'clip_storage_root', tmp_path)
    monkeypatch.setattr(service, 'inspect_export', lambda *_args: {'duration': 20})
    monkeypatch.setattr(service, 'resolve_acceleration', lambda: {'whisper_device': 'cpu',
                        'whisper_compute_type': 'int8'})
    source_file = tmp_path / 'source.mp4'
    source_file.write_bytes(b'source')
    monkeypatch.setattr(service, 'prepare_source', lambda _source: (source_file, {}))
    def thumbnail(command):
        Path(command[-1]).write_bytes(b'jpeg')
    monkeypatch.setattr(service, 'run', thumbnail)
    edited = tmp_path / 'edited.mp4'
    edited.write_bytes(b'edited')
    source_id, job_id, clip_id = (uuid4().hex for _ in range(3))
    with SessionLocal() as db:
        db.add(Source(id=source_id, kind='local', uri=str(source_file)))
        db.add(Job(id=job_id, source_id=source_id, config={'intervals': []}))
        db.add(Clip(id=clip_id, job_id=job_id, start=1, end=21, status='approved',
                    title='Initial', review={'probe': {'has_audio': False}}, artifacts={}))
        db.commit()
    result = service.finalize_cutwright(clip_id, str(edited))
    assert Path(result['artifacts']['mp4']).name == '01 - Initial.mp4'
    assert all(Path(path).is_file() for path in result['artifacts'].values())
    updated = service.rename_clip(clip_id, 'Shark: <attack>?')
    assert updated['title'] == 'Shark: <attack>?'
    assert Path(updated['artifacts']['mp4']).name == '01 - Shark attack.mp4'
    assert not Path(result['artifacts']['mp4']).exists()
    assert all(Path(path).is_file() for path in updated['artifacts'].values())
    manifest = json.loads(Path(updated['artifacts']['manifest']).read_text(encoding='utf-8'))
    assert manifest['title'] == updated['title']
    assert manifest['artifacts'] == updated['artifacts']


def test_direct_export_writes_matching_bundle(tmp_path, monkeypatch):
    Base.metadata.create_all(engine)
    monkeypatch.setattr(service.settings, 'clip_storage_root', tmp_path)
    monkeypatch.setattr(service, 'resolve_acceleration', lambda: {'whisper_device': 'cpu',
                        'whisper_compute_type': 'int8'})
    monkeypatch.setattr(service, 'inspect_export', lambda *_args: {'duration': 20})
    source_file = tmp_path / 'source.mp4'
    source_file.write_bytes(b'source')
    monkeypatch.setattr(service, 'prepare_source', lambda _source: (source_file, {'has_audio': False}))
    def render(_source, _probe, _start, _end, _srt, mp4, thumb, *_args):
        mp4.parent.mkdir(parents=True, exist_ok=True)
        mp4.write_bytes(b'video')
        thumb.write_bytes(b'jpeg')
        return {'crop_mode': 'test'}
    monkeypatch.setattr(service, 'render_vertical', render)
    source_id, job_id, clip_id = (uuid4().hex for _ in range(3))
    with SessionLocal() as db:
        db.add(Source(id=source_id, kind='local', uri=str(source_file)))
        db.add(Job(id=job_id, source_id=source_id, config={}))
        db.add(Clip(id=clip_id, job_id=job_id, start=1, end=21, status='approved',
                    title='Bigfoot Encounter', review={}, artifacts={}))
        db.commit()
    result = service.export_clip(clip_id)
    artifacts = result['artifacts']
    assert {Path(path).suffix for path in artifacts.values()} == {'.mp4', '.jpg', '.srt', '.vtt', '.json'}
    assert {Path(path).stem for path in artifacts.values()} == {'01 - Bigfoot Encounter'}
    assert all(Path(path).is_file() for path in artifacts.values())
    assert json.loads(Path(artifacts['manifest']).read_text(encoding='utf-8'))['artifacts'] == artifacts
