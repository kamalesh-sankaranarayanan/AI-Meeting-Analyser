import io
import os
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from workflow import meeting_graph
from state import load_state, save_state
# ---------------- CONFIG ----------------
PROCESSED_FILE = "processed_files.txt"
DOWNLOAD_DIR = "downloads"
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

AUDIO_TYPES = {"audio/mpeg", "audio/wav", "audio/mp3", "audio/x-wav"}
VIDEO_TYPES = {"video/mp4", "video/quicktime", "video/x-msvideo", "video/webm"}

# ---------------- INIT ----------------
creds = Credentials.from_authorized_user_file("token.json", SCOPES)
service = build("drive", "v3", credentials=creds)

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Load processed files safely
if os.path.exists(PROCESSED_FILE):
    with open(PROCESSED_FILE, "r") as f:
        state = load_state()
        processed = set(state["processed"])
else:
    processed = set()


def save_processed(file_id: str):
    if file_id not in processed:
        processed.add(file_id)
        state["processed"] = list(processed)
        save_state(state)


def download_file(file):
    filepath = os.path.join(DOWNLOAD_DIR, file["name"])

    request = service.files().get_media(fileId=file["id"])
    fh = io.FileIO(filepath, "wb")

    downloader = MediaIoBaseDownload(fh, request)

    done = False
    while not done:
        status, done = downloader.next_chunk()

    return filepath


def process_file(file):
    print("\n==============================")
    print("NEW FILE:", file["name"])

    file_id = file["id"]

    # prevent duplicate processing
    if file_id in processed:
        print("SKIPPED (already processed)")
        return

    mime = file["mimeType"]

    if mime not in AUDIO_TYPES and mime not in VIDEO_TYPES:
        return

    # mark EARLY to avoid duplicate runs if crash happens
    save_processed(file_id)

    filepath = download_file(file)
    print("Downloaded:", filepath)

    try:
        result = meeting_graph.invoke({
            "audio_path": filepath
        })

        print("\nRESULT:")
        print(result)
        print("Meeting processed successfully")

    except Exception as e:
        print("ERROR in pipeline:", e)
        # optional: remove from processed if you want retry
        # processed.remove(file_id)


def run_drive_check():
    results = service.files().list(
        pageSize=10,
        fields="files(id,name,mimeType)"
    ).execute()

    files = results.get("files", [])

    for file in files:
        process_file(file)


if __name__ == "__main__":
    run_drive_check()