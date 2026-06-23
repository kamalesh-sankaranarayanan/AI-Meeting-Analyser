import sqlite3
from datetime import datetime


DB_PATH = "database.db"


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=MEMORY")
    conn.row_factory = sqlite3.Row
    return conn


def _columns(cursor, table):
    cursor.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cursor.fetchall()}


def _add_column(cursor, table, column, definition):
    if column not in _columns(cursor, table):
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=MEMORY")
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS meetings(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            transcript TEXT,
            summary TEXT,
            risk TEXT,
            created_at TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tasks(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            meeting_id INTEGER NOT NULL,
            task TEXT NOT NULL,
            owner TEXT,
            deadline TEXT,
            priority TEXT CHECK(priority IN ('High', 'Medium', 'Low')) DEFAULT 'Medium',
            status TEXT CHECK(status IN ('Pending', 'In Progress', 'Completed')) DEFAULT 'Pending',
            created_at TEXT NOT NULL,
            FOREIGN KEY (meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS processing_jobs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            file_path TEXT,
            status TEXT NOT NULL DEFAULT 'Queued',
            stage TEXT NOT NULL DEFAULT 'Queued',
            progress INTEGER NOT NULL DEFAULT 0,
            message TEXT,
            meeting_id INTEGER,
            error TEXT,
            created_at TEXT NOT NULL,
            last_updated TEXT NOT NULL,
            completed_at TEXT
        )
    """)

    _add_column(cursor, "meetings", "last_updated", "TEXT")
    _add_column(cursor, "meetings", "file_hash", "TEXT")
    _add_column(cursor, "meetings", "status", "TEXT DEFAULT 'Processed'")
    _add_column(cursor, "meetings", "duplicate_of", "INTEGER")
    _add_column(cursor, "meetings", "speaker_map", "TEXT")

    _add_column(cursor, "tasks", "last_updated", "TEXT")
    _add_column(cursor, "tasks", "completed_at", "TEXT")
    _add_column(cursor, "tasks", "task_key", "TEXT")
    _add_column(cursor, "tasks", "duplicate_count", "INTEGER DEFAULT 0")
    _add_column(cursor, "tasks", "reminder_sent_at", "TEXT")
    _add_column(cursor, "tasks", "escalated_at", "TEXT")
    _add_column(cursor, "tasks", "escalation_level", "INTEGER DEFAULT 0")

    try:
        _add_column(cursor, "processing_jobs", "source", "TEXT DEFAULT 'manual'")
        _add_column(cursor, "processing_jobs", "source_id", "TEXT")
    except sqlite3.Error:
        pass

    cursor.execute("UPDATE meetings SET last_updated=created_at WHERE last_updated IS NULL")
    cursor.execute("UPDATE tasks SET last_updated=created_at WHERE last_updated IS NULL")
    cursor.execute("UPDATE tasks SET duplicate_count=0 WHERE duplicate_count IS NULL")
    cursor.execute("UPDATE tasks SET escalation_level=0 WHERE escalation_level IS NULL")
    cursor.execute("UPDATE meetings SET status='Processed' WHERE status IS NULL")

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tasks_meeting_id ON tasks(meeting_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tasks_task_key ON tasks(task_key)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tasks_last_updated ON tasks(last_updated)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_meetings_created_at ON meetings(created_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_meetings_file_hash ON meetings(file_hash)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_processing_jobs_status ON processing_jobs(status)")
    if {"source", "source_id"}.issubset(_columns(cursor, "processing_jobs")):
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_processing_jobs_source ON processing_jobs(source, source_id)")

    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print("Database initialized successfully with current schema")
