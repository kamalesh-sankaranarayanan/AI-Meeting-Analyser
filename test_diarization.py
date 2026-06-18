from dotenv import load_dotenv
import os

load_dotenv()

from pyannote.audio import Pipeline

pipeline = Pipeline.from_pretrained(
    "pyannote/speaker-diarization-3.1",
    token=os.getenv("HUGGINGFACE_API_KEY")
)

print("Loaded successfully")