import sqlite3
 
conn = sqlite3.connect("database.db")
cursor = conn.cursor()
 
# Meetings Table (create first - no dependencies)
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
 
# Tasks Table (with foreign key to meetings)
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
 
# Create indexes for faster queries
cursor.execute("""
CREATE INDEX IF NOT EXISTS idx_tasks_meeting_id ON tasks(meeting_id)
""")
 
cursor.execute("""
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)
""")
 
cursor.execute("""
CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority)
""")
 
cursor.execute("""
CREATE INDEX IF NOT EXISTS idx_meetings_created_at ON meetings(created_at)
""")
 
conn.commit()
conn.close()
 
print("✅ Database initialized successfully with proper schema")