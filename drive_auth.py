from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

import os
import pickle

load_dotenv()

SCOPES = ['https://www.googleapis.com/auth/drive']

CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")


def get_drive_creds():

    creds = None

    if os.path.exists("token.pickle"):
        with open("token.pickle", "rb") as token:
            creds = pickle.load(token)

    if not creds or not creds.valid:

        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())

        else:

            client_config = {
                "installed": {
                    "client_id": CLIENT_ID,
                    "client_secret": CLIENT_SECRET,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [
                        "http://localhost"
                    ]
                }
            }

            flow = InstalledAppFlow.from_client_config(
                client_config,
                SCOPES
            )

            creds = flow.run_local_server(port=0)

        with open("token.pickle", "wb") as token:
            pickle.dump(creds, token)

    return creds