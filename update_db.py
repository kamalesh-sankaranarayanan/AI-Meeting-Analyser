import sqlite3

conn = sqlite3.connect("database.db")
cursor = conn.cursor()

try:
    cursor.execute("""
    ALTER TABLE tasks
    ADD COLUMN meeting_id INTEGER
    """)
except:
    print("Column already exists")

conn.commit()
conn.close()

print("Updated")