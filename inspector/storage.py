from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3

from inspector.models import InspectionResult


@contextmanager
def connect(path: str | Path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        with db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS inspections (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
                    filename TEXT NOT NULL, input_hash TEXT NOT NULL,
                    width INTEGER NOT NULL, height INTEGER NOT NULL,
                    pipeline_version TEXT NOT NULL, profile_json TEXT NOT NULL,
                    status TEXT NOT NULL, quality_status TEXT NOT NULL,
                    compliance_status TEXT NOT NULL, result_json TEXT NOT NULL,
                    original_png BLOB NOT NULL, mask_png BLOB NOT NULL,
                    overlay_png BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS check_results (
                    inspection_id TEXT NOT NULL REFERENCES inspections(id),
                    identifier TEXT NOT NULL, check_group TEXT NOT NULL,
                    status TEXT NOT NULL, measurement_json TEXT NOT NULL,
                    threshold_json TEXT NOT NULL, explanation TEXT NOT NULL,
                    PRIMARY KEY (inspection_id, identifier)
                );
            """)
        yield db
    finally:
        db.close()


def save_inspection(result: InspectionResult, filename: str, path: str | Path) -> None:
    with connect(path) as db, db:
        db.execute("INSERT INTO inspections VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            result.inspection_id, result.created_at, filename[:255], result.input_hash,
            result.width, result.height, result.pipeline_version,
            json.dumps(result.profile, allow_nan=False), result.status,
            result.quality_status, result.compliance_status, result.to_json(),
            result.original_png, result.mask_png, result.overlay_png,
        ))
        db.executemany("INSERT INTO check_results VALUES (?,?,?,?,?,?,?)", [
            (result.inspection_id, c.identifier, c.group, c.status,
             json.dumps(c.measurement, allow_nan=False), json.dumps(c.threshold, allow_nan=False), c.explanation)
            for c in result.checks
        ])


def list_inspections(path: str | Path, limit: int = 100) -> list[dict]:
    with connect(path) as db:
        return [dict(row) for row in db.execute(
            "SELECT id, created_at, filename, status, quality_status, compliance_status FROM inspections ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )]


def load_inspection(inspection_id: str, path: str | Path) -> dict | None:
    with connect(path) as db:
        row = db.execute("SELECT * FROM inspections WHERE id = ?", (inspection_id,)).fetchone()
        if row is None:
            return None
        return dict(row)
