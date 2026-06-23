import json
import os
from storage import BASE_DIR, ensure_storage_dirs

STATE_FILE = os.getenv("STATE_FILE", os.path.join(BASE_DIR, "state.json"))

def load_state():
    ensure_storage_dirs()
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {"processed": []}

def save_state(state):
    ensure_storage_dirs()
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)
