import logging
import sqlite3
import hashlib
from datetime import datetime

from storage import DB_PATH


logger = logging.getLogger(__name__)


def current_time():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=MEMORY")
    conn.row_factory = sqlite3.Row
    return conn


def _has_columns(cursor, table, columns):
    cursor.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in cursor.fetchall()}
    return all(column in existing for column in columns)


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_processing_job(filename, filepath, source="manual", source_id=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    now = current_time()
    if _has_columns(cursor, "processing_jobs", ["source", "source_id"]):
        cursor.execute("""
            INSERT INTO processing_jobs
            (filename, file_path, status, stage, progress, message, created_at, last_updated, source, source_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (filename, filepath, "Queued", "Queued", 0, f"Waiting to start ({source})", now, now, source, source_id))
    else:
        cursor.execute("""
            INSERT INTO processing_jobs
            (filename, file_path, status, stage, progress, message, created_at, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (filename, filepath, "Queued", "Queued", 0, f"Waiting to start ({source})", now, now))
    job_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return job_id


def find_duplicate_meeting(file_hash):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id
        FROM meetings
        WHERE file_hash=?
        LIMIT 1
    """, (file_hash,))
    row = cursor.fetchone()
    conn.close()
    return row["id"] if row else None


def run_processing_job(job_id, filepath):
    from processing import update_processing_job
    from workflow import meeting_graph

    try:
        update_processing_job(job_id, "Starting", 5, message="Preparing meeting workflow")
        meeting_graph.invoke({"audio_path": filepath, "job_id": job_id})
    except Exception as exc:
        logger.error(f"Background processing failed for job {job_id}: {exc}", exc_info=True)
        update_processing_job(job_id, "Failed", 100, status="Failed", error=str(exc), message="Processing failed")


def create_job_or_duplicate(filename, filepath, source="manual", source_id=None):
    upload_hash = file_sha256(filepath)
    duplicate_id = find_duplicate_meeting(upload_hash)
    if duplicate_id:
        return {"duplicate_meeting_id": duplicate_id, "job_id": None}

    job_id = create_processing_job(filename, filepath, source=source, source_id=source_id)
    return {"duplicate_meeting_id": None, "job_id": job_id}
