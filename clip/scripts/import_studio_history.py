"""Import existing Clip jobs from older SQLite databases into the configured database."""

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from clip_service.db import Clip, Job, SessionLocal, Source, init_db


def decoded(value, default):
    if value is None:
        return default
    return json.loads(value) if isinstance(value, str) else value


def timestamp(value):
    return datetime.fromisoformat(value) if value else None


def import_database(path: Path) -> dict:
    result = {'sources': 0, 'jobs': 0, 'clips': 0}
    with sqlite3.connect(f'file:{path.resolve().as_posix()}?mode=ro', uri=True) as legacy:
        legacy.row_factory = sqlite3.Row
        with SessionLocal() as db:
            for row in legacy.execute('SELECT * FROM sources'):
                if db.get(Source, row['id']):
                    continue
                item = Source(id=row['id'], kind=row['kind'], uri=row['uri'],
                              status=row['status'], created_at=timestamp(row['created_at']))
                db.add(item)
                result['sources'] += 1
            db.flush()
            for row in legacy.execute('SELECT * FROM jobs'):
                if db.get(Job, row['id']):
                    continue
                if not db.get(Source, row['source_id']):
                    continue
                item = Job(id=row['id'], source_id=row['source_id'], status=row['status'],
                           attempts=row['attempts'], error=row['error'],
                           config=decoded(row['config'], {}),
                           created_at=timestamp(row['created_at']),
                           updated_at=timestamp(row['updated_at']))
                db.add(item)
                result['jobs'] += 1
            db.flush()
            columns = {column[1] for column in legacy.execute('PRAGMA table_info(clips)')}
            for row in legacy.execute('SELECT * FROM clips'):
                if db.get(Clip, row['id']) or not db.get(Job, row['job_id']):
                    continue
                item = Clip(id=row['id'], job_id=row['job_id'], start=row['start'],
                            end=row['end'], score=row['score'], confidence=row['confidence'],
                            status=row['status'], selection=row['selection'] if 'selection' in columns else 'unselected',
                            title=row['title'] if 'title' in columns else '',
                            artifacts=decoded(row['artifacts'], {}),
                            review=decoded(row['review'], {}) if 'review' in columns else {})
                db.add(item)
                result['clips'] += 1
            db.commit()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('databases', nargs='+', type=Path)
    args = parser.parse_args()
    init_db()
    for path in args.databases:
        if not path.is_file():
            parser.error(f'database not found: {path}')
        print(path, import_database(path))


if __name__ == '__main__':
    main()
