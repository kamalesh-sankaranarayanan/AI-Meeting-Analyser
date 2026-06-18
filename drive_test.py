from googleapiclient.discovery import build
from drive_auth import get_drive_creds

service = build(
    "drive",
    "v3",
    credentials=get_drive_creds()
)

results = service.files().list(
    pageSize=20,
    fields="files(id,name)"
).execute()

files = results.get("files", [])

for file in files:
    print(file["name"])