import pytest
import json
import wave
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from clip_service import api, voiceover
from clip_service.db import Base, Voiceover


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path / "voice.db"}')
    Base.metadata.create_all(engine)
    monkeypatch.setattr(voiceover, 'SessionLocal', sessionmaker(bind=engine, expire_on_commit=False))
    monkeypatch.setattr(api, 'SessionLocal', voiceover.SessionLocal)
    monkeypatch.setattr(voiceover, 'probe_video', lambda _: {'duration': 10.0, 'has_audio': True})
    return tmp_path


def test_draft_must_be_edited_and_approved_before_render(isolated_db, monkeypatch):
    source = isolated_db / 'finished.mp4'
    source.write_bytes(b'mp4')
    created = voiceover.create(None, str(source), 'intro')
    assert created['status'] == 'queued'
    assert not created['artifacts']
    monkeypatch.setattr(voiceover, '_draft', lambda *_: [{'start': 0, 'end': 4, 'text': 'Начало'}])
    draft = voiceover.generate_draft(created['id'])
    assert draft['status'] == 'draft'
    edited = voiceover.edit(created['id'], [{'start': 1, 'end': 5, 'text': 'Новый текст'}])
    assert edited['lines'][0]['text'] == 'Новый текст'
    assert voiceover.approve(created['id'])['status'] == 'approved'
    with pytest.raises(ValueError):
        voiceover.approve(created['id'])


def test_lines_must_fit_and_not_overlap():
    with pytest.raises(ValueError):
        voiceover.validate_lines([{'start': 0, 'end': 2, 'text': 'a'},
                                  {'start': 1, 'end': 3, 'text': 'b'}], 10)
    with pytest.raises(ValueError):
        voiceover.validate_lines([{'start': 9, 'end': 11, 'text': 'a'}], 10)
    with pytest.raises(ValueError):
        voiceover.validate_lines([], 10)
    with pytest.raises(ValueError, match='repeated'):
        voiceover.validate_draft_quality([{'text': 'Вода поднимается'}, {'text': 'Вода поднимается'}])


def test_long_speech_returns_to_editable_draft(isolated_db, monkeypatch):
    source = isolated_db / 'finished.mp4'
    source.write_bytes(b'mp4')
    item = voiceover.create(None, str(source), 'commentary')
    monkeypatch.setattr(voiceover, '_draft', lambda *_: [{'start': 0, 'end': 1, 'text': 'Слишком длинно'}])
    voiceover.generate_draft(item['id'])
    voiceover.approve(item['id'])

    def long_speech(_text, target):
        with wave.open(str(target), 'wb') as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(8000)
            output.writeframes(b'\0\0' * 16000)

    monkeypatch.setattr(voiceover, '_speak', long_speech)
    monkeypatch.setattr(voiceover.settings, 'clip_storage_root', isolated_db)
    result = voiceover.render(item['id'])
    assert result['status'] == 'needs_revision'
    assert not result['artifacts']
    voiceover.edit(item['id'], [{'start': 0, 'end': 3, 'text': 'Коротко'}])
    assert voiceover.approve(item['id'])['status'] == 'approved'


def test_artifact_requires_auth_and_serves_audio(isolated_db):
    source = isolated_db / 'finished.mp4'
    source.write_bytes(b'mp4')
    item = voiceover.create(None, str(source), 'intro')
    track = isolated_db / 'voice.wav'
    track.write_bytes(b'RIFFtest')
    with voiceover.SessionLocal() as db:
        row = db.get(Voiceover, item['id'])
        row.artifacts = {'wav': str(track)}
        db.commit()
    client = TestClient(api.app)
    route = f'/voiceovers/{item["id"]}/artifacts/wav'
    assert client.get(route).status_code == 401
    response = client.get(route, headers={'X-API-Key': api.settings.clip_api_key})
    assert response.status_code == 200
    assert response.content == b'RIFFtest'


def test_description_rejects_source_speech_overlap(isolated_db, monkeypatch):
    monkeypatch.setattr(voiceover.settings, 'clip_storage_root', isolated_db)
    source = isolated_db / 'finished.mp4'
    source.write_bytes(b'mp4')
    item = voiceover.create(None, str(source), 'description')
    monkeypatch.setattr(voiceover, '_draft', lambda *_: [{'start': 1, 'end': 3, 'text': 'Видна река'}])
    voiceover.generate_draft(item['id'])
    work = isolated_db / 'voiceovers' / item['id']
    (work / 'transcript-auto.json').write_text(json.dumps({'segments': [
        {'start': 2, 'end': 4, 'text': 'speech'}]}), encoding='utf-8')
    with pytest.raises(ValueError, match='pause'):
        voiceover.approve(item['id'])
    voiceover.edit(item['id'], [{'start': 5, 'end': 8, 'text': 'Видна река'}])
    assert voiceover.approve(item['id'])['status'] == 'approved'


def test_speech_gaps_respect_edges_and_merge_overlapping_speech():
    gaps = voiceover.speech_gaps([
        {'start': 1, 'end': 3}, {'start': 2, 'end': 5}, {'start': 8, 'end': 9}], 12)
    assert gaps == [{'start': 5.15, 'end': 7.85}, {'start': 9.15, 'end': 12.0}]


def test_second_active_voiceover_for_source_is_rejected(isolated_db):
    source = isolated_db / 'finished.mp4'
    source.write_bytes(b'mp4')
    voiceover.create(None, str(source), 'intro')
    with pytest.raises(ValueError, match='already in progress'):
        voiceover.create(None, str(source), 'commentary')
