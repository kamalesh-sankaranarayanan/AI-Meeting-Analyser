import time
from drive_watcher import run_drive_check

POLL_INTERVAL = 60  # seconds

print("🚀 AI Meeting System Started...")

while True:
    try:
        run_drive_check()
    except Exception as e:
        print("Error:", e)

    print("⏳ Waiting for next check...")
    time.sleep(POLL_INTERVAL)