from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

creds = Credentials.from_authorized_user_file(
    "token.json",
    SCOPES
)

service = build("drive", "v3", credentials=creds)

results = service.files().list(
    pageSize=20,
    fields="files(id,name,mimeType)"
).execute()

files = results.get("files", [])

print(f"Found {len(files)} files")

for file in files:
    print(
        f"Name: {file['name']} | "
        f"Type: {file['mimeType']} | "
        f"ID: {file['id']}"
    )