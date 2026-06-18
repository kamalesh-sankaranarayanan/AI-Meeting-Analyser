import subprocess
import os

def extract_audio(video_path: str) -> str:
    """
    Convert video file → audio file using ffmpeg
    """
    audio_path = os.path.splitext(video_path)[0] + ".wav"

    command = [
        "ffmpeg",
        "-y",
        "-i",
        video_path,
        "-ac",
        "1",
        "-ar",
        "16000",
        audio_path
    ]

    subprocess.run(command, check=True)

    return audio_path