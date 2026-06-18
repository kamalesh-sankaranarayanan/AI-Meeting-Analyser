import whisper
import sqlite3
import json
import os

from datetime import datetime
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(
    api_key=os.getenv("OPENROUTER_API_KEY"),
    base_url="https://openrouter.ai/api/v1"
)

print("Loading Whisper...")
model = whisper.load_model("small")


def process_meeting(audio_path):

    print("Transcribing audio...")

    result = model.transcribe(
        audio_path,
        language="en"
    )

    transcript = result["text"]

    # SUMMARY

    summary_prompt = f"""
Summarize this meeting in 5 concise points.

Transcript:
{transcript}
"""

    summary_response = client.chat.completions.create(
        model="openrouter/free",
        messages=[
            {
                "role": "user",
                "content": summary_prompt
            }
        ]
    )

    summary_text = summary_response.choices[0].message.content

    # TASK EXTRACTION

    task_prompt = f"""
Extract all tasks.

Return ONLY valid JSON.

Priority Rules:

High:
- Critical deliverables
- Deadline within 2 days

Medium:
- Due this week

Low:
- No specific deadline

Format:

[
  {{
    "task":"Prepare SRS",
    "owner":"Kamalesh",
    "deadline":"Friday",
    "priority":"High"
  }}
]

Transcript:
{transcript}
"""
    task_response = client.chat.completions.create(
        model="openrouter/free",
        messages=[
            {
                "role": "user",
                "content": task_prompt
            }
        ],
        temperature=0
    )

    raw_output = task_response.choices[0].message.content

    print(raw_output)

    raw_output = raw_output.replace(
        "```json",
        ""
    )

    raw_output = raw_output.replace(
        "```",
        ""
    )

    raw_output = raw_output.strip()

    try:

        tasks = json.loads(raw_output)

        if isinstance(tasks, dict):
            tasks = [tasks]

    except:

        tasks = []

    # RISK DETECTION

    risk_prompt = f"""
Analyze this meeting.

Find:

1. Risks
2. Delays
3. Blockers

Give concise bullet points.

Transcript:
{transcript}
"""

    risk_response = client.chat.completions.create(
        model="openrouter/free",
        messages=[
            {
                "role": "user",
                "content": risk_prompt
            }
        ]
    )

    risk_text = risk_response.choices[0].message.content

    # DATABASE

    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()

    for t in tasks:

        if not isinstance(t, dict):
            continue

        cursor.execute(
            """
            INSERT INTO tasks
            (
                task,
                owner,
                deadline,
                priority,
                status,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                t.get("task", ""),
                t.get("owner", ""),
                t.get("deadline", ""),
                t.get("priority", "Medium"),
                "Pending",
                datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            )
        )

    cursor.execute(
        """
        INSERT INTO meetings
        (
            filename,
            transcript,
            summary,
            risk,
            created_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            os.path.basename(audio_path),
            transcript,
            summary_text,
            risk_text,
            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        )
    )

    conn.commit()
    conn.close()

    return (
        transcript,
        summary_text,
        risk_text
    )