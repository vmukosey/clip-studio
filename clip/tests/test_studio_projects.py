from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from clip_service import api, studio_projects, voiceover
from clip_service.db import Base, Clip, Job, Source, StudioAsset


def client_for(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path / "studio.db"}')
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(studio_projects, 'SessionLocal', sessions)
    monkeypatch.setattr(voiceover, 'SessionLocal', sessions)
    monkeypatch.setattr(studio_projects, 'root', tmp_path / 'storage' / 'studio')
    monkeypatch.setattr(studio_projects, 'probe_video', lambda _: {'duration': 7.5})
    monkeypatch.setattr(voiceover, 'probe_video', lambda _: {'duration': 7.5})
    return TestClient(api.app), sessions


def headers():
    return {'X-API-Key': api.settings.clip_api_key}


def document(media=None):
    return {'version': 1, 'name': 'Монтаж', 'width': 1080, 'height': 1920, 'fps': 30,
            'media': media or [], 'clips': [], 'transitions': [], 'subtitles': [],
            'customTransitions': []}


def test_project_autosave_conflict_and_media_persistence(tmp_path, monkeypatch):
    client, _ = client_for(tmp_path, monkeypatch)
    assert client.get('/studio/projects').status_code == 401
    created = client.post('/studio/projects', headers=headers(), json={'document': document()})
    assert created.status_code == 200
    project = created.json()
    uploaded = client.post('/studio/assets/upload', headers=headers(),
                           files={'file': ('frame.png', b'png-bytes', 'image/png')})
    assert uploaded.status_code == 200
    asset = uploaded.json()
    assert client.get(asset['url']).status_code == 401
    assert client.get(asset['url'], headers=headers()).content == b'png-bytes'
    next_document = document([{'id': 'm1', 'name': 'frame.png', 'kind': 'image',
                               'src': asset['url'], 'duration': 5, 'color': '#ffffff'}])
    saved = client.put(f"/studio/projects/{project['id']}", headers=headers(),
                       json={'document': next_document, 'expected_revision': 1})
    assert saved.status_code == 200
    assert saved.json()['revision'] == 2
    assert client.put(f"/studio/projects/{project['id']}", headers=headers(),
                      json={'document': next_document, 'expected_revision': 1}).status_code == 409
    reopened = client.get(f"/studio/projects/{project['id']}", headers=headers()).json()
    assert reopened['document']['media'][0]['src'] == asset['url']
    next_document['media'][0]['src'] = 'C:\\missing.mp4'
    assert client.put(f"/studio/projects/{project['id']}", headers=headers(),
                      json={'document': next_document, 'expected_revision': 2}).status_code == 422


def test_old_work_search_and_voiceover_regions(tmp_path, monkeypatch):
    client, sessions = client_for(tmp_path, monkeypatch)
    export = tmp_path / 'old.mp4'
    export.write_bytes(b'mp4')
    with sessions() as db:
        db.add(Source(id='source', kind='local', uri=str(export), status='ready'))
        db.add(Job(id='job', source_id='source', status='exported', config={}))
        db.add(Clip(id='clip', job_id='job', start=0, end=7.5, title='Старый ролик',
                    status='exported', artifacts={'mp4': str(export)}))
        db.commit()
    rows = client.get('/studio/library?search=Старый', headers=headers()).json()
    assert rows[0]['clips'][0]['available'] is True
    imported = client.post('/studio/assets/from-clip/clip', headers=headers()).json()
    with sessions() as db:
        assert db.get(StudioAsset, imported['id']).path
    project = client.post('/studio/projects', headers=headers(), json={'document': document()}).json()
    render = client.post(f"/studio/projects/{project['id']}/renders", headers=headers(),
                         files={'file': ('render.mp4', b'mp4', 'video/mp4')}).json()
    created = voiceover.create(None, None, 'commentary', render_id=render['id'],
                               prompt='Коротко', regions=[{'start': 1, 'end': 3}], manual=True)
    assert created['status'] == 'draft'
    assert created['prompt'] == 'Коротко'
    assert created['project_id'] == project['id']
    try:
        voiceover.edit(created['id'], [{'start': 4, 'end': 5, 'text': 'Тест'}])
        assert False, 'line outside selected region must fail'
    except ValueError as exc:
        assert 'regions' in str(exc)
    edited = voiceover.edit(created['id'], [{'start': 1, 'end': 2.5, 'text': 'Видна дорога'}])
    assert edited['lines'][0]['start'] == 1
