"""Move the three reviewed GTA exports into the readable export layout."""
import json
import sqlite3
from pathlib import Path

from clip_service.export_names import safe_name


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / 'gaming-run.db'
STORAGE = ROOT / 'gaming-run-storage'
JOB_ID = 'ffcd04e6159b1bccfd7e7f35'
TITLES = {
    '6c48139063b276c252145db1': 'Bigfoot Encounter',
    'da0113b0083d51b5b1e9074f': 'Shark Encounter',
    '4e706ac3d5d4783b89678cb3': 'In-Game Social Feed',
}


def main():
    with sqlite3.connect(DB) as db:
        db.row_factory = sqlite3.Row
        if 'title' not in {column[1] for column in db.execute('PRAGMA table_info(clips)')}:
            db.execute("ALTER TABLE clips ADD COLUMN title VARCHAR(200) DEFAULT ''")
        job = db.execute('SELECT * FROM jobs WHERE id=?', (JOB_ID,)).fetchone()
        source = db.execute('SELECT * FROM sources WHERE id=?', (job['source_id'],)).fetchone()
        directory = (STORAGE / 'exports' /
                     f"{safe_name(Path(source['uri']).stem)}-{source['id'][:8]}" /
                     f"{job['created_at'][:10]}-gaming-{JOB_ID[:8]}")
        rows = db.execute("SELECT * FROM clips WHERE job_id=? AND status='exported' ORDER BY start",
                          (JOB_ID,)).fetchall()
        assert {row['id'] for row in rows} == set(TITLES)
        moves = []
        for number, row in enumerate(rows, 1):
            artifacts = json.loads(row['artifacts'])
            base = f"{number:02d} - {TITLES[row['id']]}"
            new_artifacts = {}
            for key, old_value in artifacts.items():
                old = (ROOT / old_value).resolve() if not Path(old_value).is_absolute() else Path(old_value)
                new = directory / f'{base}{old.suffix}'
                if not old.is_file():
                    raise FileNotFoundError(old)
                if new.exists():
                    raise FileExistsError(new)
                moves.append((old, new))
                new_artifacts[key] = str(new)
            db.execute('UPDATE clips SET title=?, artifacts=? WHERE id=?',
                       (TITLES[row['id']], json.dumps(new_artifacts), row['id']))
        directory.mkdir(parents=True, exist_ok=True)
        try:
            for old, new in moves:
                old.rename(new)
            for row in rows:
                manifest = directory / f"{rows.index(row)+1:02d} - {TITLES[row['id']]}.json"
                payload = json.loads(manifest.read_text(encoding='utf-8'))
                payload['title'] = TITLES[row['id']]
                payload['artifacts'] = json.loads(db.execute('SELECT artifacts FROM clips WHERE id=?',
                                                             (row['id'],)).fetchone()[0])
                manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
            db.commit()
        except Exception:
            db.rollback()
            for old, new in reversed(moves):
                if new.exists():
                    new.rename(old)
            raise
        print(directory)


if __name__ == '__main__':
    main()
