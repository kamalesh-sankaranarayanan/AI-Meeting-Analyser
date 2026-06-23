import io
import json
import os
import sqlite3
import threading
from datetime import datetime

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from werkzeug.utils import secure_filename

from jobs import create_job_or_duplicate, run_processing_job
from state import load_state, save_state
from storage import DB_PATH, DOWNLOAD_DIR
# ---------------- CONFIG ----------------
PROCESSED_FILE = "processed_files.txt"
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

AUDIO_TYPES = {"audio/mpeg", "audio/wav", "audio/mp3", "audio/x-wav"}
VIDEO_TYPES = {"video/mp4", "video/quicktime", "video/x-msvideo", "video/webm"}
SUPPORTED_TYPES = AUDIO_TYPES | VIDEO_TYPES

# ---------------- INIT ----------------
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Load processed Drive file IDs safely.
state = load_state()
processed = set(state.get("processed", []))


def update_drive_status(**updates):
    status = state.get("drive_status", {})
    status.update(updates)
    status["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    state["drive_status"] = status
    save_state(state)
    return status


def get_drive_service():
    token_json = os.getenv("GOOGLE_TOKEN_JSON")
    if token_json:
        creds = Credentials.from_authorized_user_info(json.loads(token_json), SCOPES)
    elif os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    elif os.path.exists("token.pickle"):
        import pickle
        with open("token.pickle", "rb") as token:
            creds = pickle.load(token)
    else:
        raise FileNotFoundError("No Google Drive token found. Run drive_auth.py first.")

    return build("drive", "v3", credentials=creds)


def save_processed(file_id: str):
    if file_id not in processed:
        processed.add(file_id)
        state["processed"] = list(processed)
        save_state(state)


def has_recorded_drive_job(file_id: str) -> bool:
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA journal_mode=MEMORY")
        cursor = conn.cursor()
        columns = {row[1] for row in cursor.execute("PRAGMA table_info(processing_jobs)").fetchall()}
        if "source_id" not in columns:
            conn.close()
            return False

        cursor.execute("""
            SELECT id
            FROM processing_jobs
            WHERE source='drive'
              AND source_id=?
              AND status IN ('Queued', 'Processing', 'Completed')
            LIMIT 1
        """, (file_id,))
        exists = cursor.fetchone() is not None
        conn.close()
        return exists
    except sqlite3.Error:
        return False


def download_file(file):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_")
    filename = timestamp + secure_filename(file["name"])
    filepath = os.path.join(DOWNLOAD_DIR, filename)

    service = get_drive_service()
    request = service.files().get_media(fileId=file["id"])
    fh = io.FileIO(filepath, "wb")

    downloader = MediaIoBaseDownload(fh, request)

    done = False
    while not done:
        status, done = downloader.next_chunk()

    return filepath


def process_file(file, async_process=True):
    print("\n==============================")
    print("NEW FILE:", file["name"])

    file_id = file["id"]

    # Skip only if state and database agree that this Drive file has a job.
    if file_id in processed and has_recorded_drive_job(file_id):
        print("SKIPPED (already processed)")
        return

    mime = file["mimeType"]

    if mime not in SUPPORTED_TYPES:
        return

    filepath = download_file(file)
    print("Downloaded:", filepath)

    try:
        result = create_job_or_duplicate(
            file["name"],
            filepath,
            source="drive",
            source_id=file_id
        )

        save_processed(file_id)

        if result["duplicate_meeting_id"]:
            print("Duplicate meeting:", result["duplicate_meeting_id"])
            return result

        if async_process:
            worker = threading.Thread(
                target=run_processing_job,
                args=(result["job_id"], filepath),
                daemon=True
            )
            worker.start()
        else:
            run_processing_job(result["job_id"], filepath)

        print("\nRESULT:")
        print(result)
        print("Meeting processing started")
        return result

    except Exception as e:
        print("ERROR in pipeline:", e)
        return {"error": str(e), "file_id": file_id}


def run_drive_check(async_process=True):
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    update_drive_status(
        status="Checking",
        last_checked=started_at,
        files_seen=0,
        files_queued=0,
        files_skipped=0,
        duplicates=0,
        last_error=""
    )

    service = get_drive_service()
    try:
        results = service.files().list(
            pageSize=10,
            orderBy="createdTime desc",
            fields="files(id,name,mimeType,createdTime)",
            q="trashed=false"
        ).execute()

        files = results.get("files", [])

        started = []
        skipped = 0
        duplicates = 0
        for file in files:
            if file["mimeType"] not in SUPPORTED_TYPES:
                skipped += 1
                continue

            if file["id"] in processed and has_recorded_drive_job(file["id"]):
                skipped += 1
                continue

            result = process_file(file, async_process=async_process)
            if result:
                started.append(result)
                if result.get("duplicate_meeting_id"):
                    duplicates += 1

        update_drive_status(
            status="Idle",
            files_seen=len(files),
            files_queued=len([item for item in started if item.get("job_id")]),
            files_skipped=skipped,
            duplicates=duplicates,
            last_error=""
        )
        return started
    except Exception as exc:
        update_drive_status(
            status="Error",
            files_seen=0,
            files_queued=0,
            files_skipped=0,
            duplicates=0,
            last_error=str(exc)
        )
        raise


if __name__ == "__main__":
    run_drive_check()
