import json
import os

STATE_FILE = "state.json"

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {"processed": []}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)