import whisperx
import sqlite3
import json
import os
import logging
import hashlib
import re
import time
from datetime import datetime
from dotenv import load_dotenv
from openai import OpenAI
import httpx
 
HAS_PYANNOTE = None
 
from mailer import send_task_alert
from report import create_report
from schema import init_db
from storage import DB_PATH
 
# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
 
load_dotenv()
 
# ============================================================================
# ENVIRONMENT VALIDATION
# ============================================================================
 
def validate_environment():
    """Validate required environment variables"""
    required_keys = ["OPENROUTER_API_KEY", "HUGGINGFACE_API_KEY"]
    missing = [key for key in required_keys if not os.getenv(key)]
    
    if missing:
        logger.warning(f"âš ï¸ Missing environment variables: {', '.join(missing)}")
        if "HUGGINGFACE_API_KEY" in missing:
            logger.warning("   Hugging Face key required for WhisperX and Pyannote")
    else:
        logger.info("âœ… Environment variables validated")
 
validate_environment()
try:
    init_db()
except sqlite3.Error as exc:
    logger.warning(f"Database migration skipped: {exc}")


def current_time():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def normalize_text(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value


def task_key(task: str, owner: str = "") -> str:
    return f"{normalize_text(owner or 'Unassigned')}::{normalize_text(task)}"


def normalize_owner(owner: str) -> str:
    owner = str(owner or "").strip()
    if not owner:
        return "Unassigned"

    normalized = normalize_text(owner)
    pronouns = {
        "i",
        "me",
        "my",
        "myself",
        "we",
        "us",
        "our",
        "ourselves",
        "speaker",
        "unknown",
        "none",
        "na",
        "n a",
    }
    if normalized in pronouns:
        return "Unassigned"

    return owner


def file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def update_processing_job(job_id, stage, progress, status="Processing", message=None, error=None, meeting_id=None):
    if not job_id:
        return

    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA journal_mode=MEMORY")
        cursor = conn.cursor()
        completed_at = current_time() if status in ("Completed", "Failed", "Duplicate") else None
        cursor.execute("""
            UPDATE processing_jobs
            SET stage=?,
                progress=?,
                status=?,
                message=COALESCE(?, message),
                error=COALESCE(?, error),
                meeting_id=COALESCE(?, meeting_id),
                last_updated=?,
                completed_at=COALESCE(?, completed_at)
            WHERE id=?
        """, (stage, progress, status, message, error, meeting_id, current_time(), completed_at, job_id))
        conn.commit()
    except sqlite3.Error as exc:
        logger.warning(f"Unable to update processing job {job_id}: {exc}")
    finally:
        if conn:
            conn.close()


def call_llm_with_retries(**kwargs):
    last_error = None
    for attempt in range(3):
        try:
            return client.chat.completions.create(**kwargs)
        except Exception as exc:
            last_error = exc
            wait = 2 ** attempt
            logger.warning(f"LLM call failed on attempt {attempt + 1}/3: {exc}")
            time.sleep(wait)
    raise last_error
 
# ============================================================================
# INITIALIZE CLIENTS
# ============================================================================
 
client = OpenAI(
    api_key=os.getenv("OPENROUTER_API_KEY"),
    base_url="https://openrouter.ai/api/v1",
    timeout=httpx.Timeout(30.0)
)
 
HF_TOKEN = os.getenv("HUGGINGFACE_API_KEY")
LLM_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
WHISPERX_MODEL_SIZE = os.getenv("WHISPERX_MODEL_SIZE", "small")
ENABLE_AUDIO_TRANSCRIPTION = os.getenv("ENABLE_AUDIO_TRANSCRIPTION", "true").lower() == "true"
ENABLE_DIARIZATION = os.getenv("ENABLE_DIARIZATION", "true").lower() == "true"
 
# ============================================================================
# MODEL LOADING (Lazy - loaded once)
# ============================================================================
 
_WHISPERX_MODEL = None
_DIARIZATION_PIPELINE = None
 
def get_whisperx_model():
    """
    Load WhisperX model (REPLACES basic Whisper)
    
    Improvements over Whisper:
    - Better accuracy
    - Word-level timestamps
    - Automatic language detection
    - Proper audio alignment
    """
    global _WHISPERX_MODEL
    if _WHISPERX_MODEL is None:
        try:
            logger.info(f"Loading WhisperX model ({WHISPERX_MODEL_SIZE})...")
            _WHISPERX_MODEL = whisperx.load_model(WHISPERX_MODEL_SIZE, device="cpu", compute_type="int8")
            logger.info("âœ… WhisperX model loaded successfully")
        except Exception as e:
            logger.error(f"Failed to load WhisperX: {e}")
            raise
    return _WHISPERX_MODEL
 
 
def get_diarization_pipeline():
    """
    Load Pyannote speaker diarization pipeline
    
    Identifies:
    - Who spoke
    - When they spoke
    - Speaker boundaries
    
    Requires Hugging Face token
    """
    global _DIARIZATION_PIPELINE, HAS_PYANNOTE
    if not ENABLE_DIARIZATION:
        logger.info("Speaker diarization disabled by ENABLE_DIARIZATION=false")
        return None

    if _DIARIZATION_PIPELINE is None:
        if HAS_PYANNOTE is False:
            logger.warning("âš ï¸ Pyannote not installed. Speaker diarization disabled.")
            return None
        
        if not HF_TOKEN:
            logger.warning("âš ï¸ HUGGINGFACE_API_KEY not set. Speaker diarization disabled.")
            return None
        
        try:
            from pyannote.audio import Pipeline
            HAS_PYANNOTE = True
            logger.info("Loading Pyannote diarization pipeline...")
            _DIARIZATION_PIPELINE = Pipeline.from_pretrained(
                                    "pyannote/speaker-diarization-3.1",
                                    token=HF_TOKEN
                                    )
            logger.info("âœ… Pyannote diarization pipeline loaded successfully")
        except Exception as e:
            HAS_PYANNOTE = False
            logger.warning(f"Failed to load Pyannote: {e}")
            return None
    
    return _DIARIZATION_PIPELINE


def run_diarization(diarization_pipeline, audio_path: str):
    """
    Run Pyannote diarization without relying on TorchCodec's file decoder.

    Some Windows/PyTorch/TorchCodec combinations load the Pyannote pipeline but
    fail when Pyannote tries to decode a filename directly. WhisperX already
    gives us a reliable 16 kHz mono loader, so pass preloaded waveform data.
    """
    try:
        return diarization_pipeline(audio_path)
    except Exception as first_error:
        first_error_text = str(first_error)
        logger.warning(f"Path-based diarization failed: {first_error_text}. Retrying with preloaded audio.")

    try:
        import torch

        audio = whisperx.load_audio(audio_path)
        waveform = torch.from_numpy(audio).float().unsqueeze(0)
        return diarization_pipeline({
            "waveform": waveform,
            "sample_rate": 16000
        })
    except Exception as second_error:
        raise RuntimeError(f"{first_error_text}; preloaded audio retry failed: {second_error}")


def iter_diarization_tracks(diarization):
    """Yield (turn, speaker) pairs from Pyannote 3 Annotation or Pyannote 4 output."""
    annotation = diarization
    if hasattr(diarization, "speaker_diarization"):
        annotation = diarization.speaker_diarization

    if hasattr(annotation, "itertracks"):
        for turn, _, speaker in annotation.itertracks(yield_label=True):
            yield turn, speaker
        return

    raise TypeError(f"Unsupported diarization output type: {type(diarization).__name__}")


def normalize_speaker_label(speaker):
    match = re.search(r"(\d+)", str(speaker or ""))
    if match:
        return f"SPEAKER_{int(match.group(1)):02d}"
    return str(speaker or "Unknown").strip() or "Unknown"


def segment_overlap(start_a, end_a, start_b, end_b):
    return max(0, min(end_a, end_b) - max(start_a, start_b))


def speaker_for_segment(segment, diarization_turns):
    if not diarization_turns:
        return "Unknown"

    segment_start = float(segment.get("start", 0) or 0)
    segment_end = float(segment.get("end", segment_start) or segment_start)
    if segment_end <= segment_start:
        segment_end = segment_start + 0.01

    best_speaker = "Unknown"
    best_overlap = 0
    for turn_start, turn_end, speaker in diarization_turns:
        overlap = segment_overlap(segment_start, segment_end, turn_start, turn_end)
        if overlap > best_overlap:
            best_overlap = overlap
            best_speaker = speaker

    if best_overlap > 0:
        return best_speaker

    midpoint = (segment_start + segment_end) / 2
    nearest_turn = min(
        diarization_turns,
        key=lambda item: min(abs(midpoint - item[0]), abs(midpoint - item[1])),
        default=None
    )
    return nearest_turn[2] if nearest_turn else "Unknown"
 
 
# ============================================================================
# TRANSCRIPTION WITH SPEAKER DIARIZATION
# ============================================================================
 
def transcribe_audio(audio_path: str) -> dict:
    """
    Transcribe audio with speaker identification
    
    MAJOR FIX: Replaces basic Whisper with WhisperX + Pyannote
    
    Args:
        audio_path: Path to audio file
        
    Returns:
        {
            'transcript': Full transcript with speakers,
            'segments': List of segments with speaker info,
            'language': Detected language
        }
        
    Raises:
        ValueError: If transcription fails
    """
    try:
        extension = os.path.splitext(audio_path)[1].lower()
        if extension == ".txt":
            logger.info(f"Reading uploaded transcript text: {audio_path}")
            with open(audio_path, "r", encoding="utf-8") as transcript_file:
                transcript = transcript_file.read().strip()
            if not transcript:
                raise ValueError("Transcript file is empty.")
            return {
                "transcript": transcript,
                "segments": [],
                "language": "en"
            }

        if not ENABLE_AUDIO_TRANSCRIPTION:
            raise ValueError(
                "Audio transcription is disabled on this deployment to avoid memory limits. "
                "Upload a .txt transcript file, or enable audio transcription on a larger instance."
            )

        logger.info(f"ðŸŽµ Transcribing audio with WhisperX: {audio_path}")
        
        # Step 1: Load WhisperX (CHANGED from: whisper.load_model("small"))
        model = get_whisperx_model()
        
        # Step 2: Transcribe with word-level timestamps
        logger.info("Processing audio through WhisperX...")
        result = model.transcribe(audio_path, language="en")
        
        logger.info(f"âœ… Transcription complete ({len(result['segments'])} segments)")
        
        # Step 3: Get speaker diarization (NEW FEATURE)
        diarization_pipeline = get_diarization_pipeline()
        diarization_turns = []
        
        if diarization_pipeline:
            try:
                logger.info("ðŸŽ¤ Running speaker diarization...")
                diarization = run_diarization(diarization_pipeline, audio_path)
                
                # Store speaker turns for overlap-based segment attribution.
                for turn, speaker in iter_diarization_tracks(diarization):
                    diarization_turns.append((
                        float(turn.start),
                        float(turn.end),
                        normalize_speaker_label(speaker)
                    ))
                
                unique_speakers = len({speaker for _, _, speaker in diarization_turns})
                logger.info(f"âœ… Diarization complete - Identified {unique_speakers} speakers")
                
            except Exception as e:
                logger.warning(f"Diarization failed: {e} - continuing without speaker info")
                diarization = None
        else:
            diarization = None
        
        # Step 4: Align transcription with speakers (NEW FEATURE)
        aligned_segments = []
        full_transcript_lines = []
        
        for segment in result['segments']:
            segment_text = segment['text'].strip()
            segment_start = segment['start']
            
            # Find speaker at this time
            speaker = speaker_for_segment(segment, diarization_turns if diarization else [])
            
            if segment_text:  # Skip empty segments
                aligned_segments.append({
                    'text': segment_text,
                    'speaker': speaker,
                    'start': segment_start,
                    'end': segment.get('end', segment_start)
                })
                
                # Build transcript line with speaker
                full_transcript_lines.append(f"[{speaker}] {segment_text}")
        
        # Full transcript with speaker labels
        full_transcript = "\n".join(full_transcript_lines)
        
        logger.info(f"âœ… Full transcript with speakers ready ({len(full_transcript)} chars)")
        
        return {
            'transcript': full_transcript,
            'segments': aligned_segments,
            'language': result.get('language', 'en')
        }
        
    except Exception as e:
        logger.error(f"âŒ Transcription failed: {e}")
        raise ValueError(f"Failed to transcribe audio: {e}")
 
 
# ============================================================================
# SUMMARY GENERATION (NO CHANGES - works with new transcript)
# ============================================================================
 
def extract_summary(transcript: str) -> str:
    """
    Generate meeting summary using LLM
    
    Args:
        transcript: Meeting transcript (now with speaker info)
        
    Returns:
        Summary text
    """
    try:
        logger.info("ðŸ“ Generating meeting summary...")
        
        summary_prompt = f"""
Summarize this meeting in 5 concise bullet points.
 
Transcript (shortened):
{transcript[:4000]}
"""
        
        response = call_llm_with_retries(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": summary_prompt}],
            max_tokens=500
        )
        

        print("SUMMARY RESPONSE:")
        print(response)
        summary = response.choices[0].message.content

        if not summary:
            logger.warning("Summary returned None")
            return "Summary generation failed"

        logger.info("âœ… Summary generated successfully")

        return summary
        
    except Exception as e:
        logger.error(f"Summary generation failed: {e}")
        return "Failed to generate summary"
 
 
# ============================================================================
# TASK EXTRACTION (IMPROVED - now has speaker context)
# ============================================================================

def build_task_extraction_prompt(transcript: str) -> str:
    return f"""
You are an expert project manager converting a meeting transcript into execution-ready tasks.

Goal:
Extract every actionable item that represents unfinished work, a commitment, a blocker to resolve, a decision that needs follow-up, or a deliverable that must be produced.

Use the transcript evidence carefully. People often imply tasks indirectly through status updates.

Strong extraction rules:
1. Extract concrete work only. Do not create tasks for casual discussion, greetings, completed work, or background context.
2. Prefer action verbs: Finalize, Create, Review, Test, Resolve, Prepare, Share, Schedule, Update, Deploy, Integrate.
3. Split unrelated work into separate tasks.
4. Merge duplicates or near-duplicates.
5. Keep each task short and specific, ideally 4-12 words.
6. If work is already clearly completed, do not extract it unless a follow-up remains.
7. If a blocker is mentioned, create a task to resolve the blocker.
8. If a risk could delay work, create a task only when an owner/action is implied.

Owner rules:
- If a named person is assigned, use that name.
- If a speaker says "I will", "I'll", "let me", or "my team will", use that speaker label/name if available.
- If a person is only mentioned as affected but not assigned, do not make them the owner.
- If ownership is unclear, use "Unassigned".
- Never invent names.
- Never return pronouns as owners. "I", "me", "we", "my team", and "us" must become a speaker label if available, otherwise "Unassigned".

Deadline rules:
- Preserve natural deadlines exactly as stated: "today", "tomorrow", "Friday", "next week", "before demo".
- If no deadline is stated or strongly implied, use "".
- Do not invent dates.

Priority rules:
- High: due today/tomorrow, urgent/asap/immediate, blocker, production issue, demo-critical, client-critical.
- Medium: due this week, named weekday, important project deliverable.
- Low: no deadline and not risky.

Status rules:
- Use "Pending" for newly extracted tasks.
- Use "In Progress" only when the transcript clearly says work has started but is unfinished.
- Do not use "Completed" for extracted tasks.

Return ONLY valid JSON. No markdown, no explanations.

JSON schema:
[
  {{
    "task": "short action item",
    "owner": "person or Unassigned",
    "deadline": "natural language deadline or empty string",
    "priority": "High | Medium | Low",
    "status": "Pending | In Progress",
    "evidence": "short transcript phrase that supports this task"
  }}
]

Examples:
"Rahul, finish the wireframes by Thursday"
=> {{"task":"Finish wireframes","owner":"Rahul","deadline":"Thursday","priority":"Medium","status":"Pending","evidence":"Rahul, finish the wireframes by Thursday"}}

"Testing has not started and the demo is tomorrow"
=> {{"task":"Start testing before demo","owner":"Unassigned","deadline":"tomorrow","priority":"High","status":"Pending","evidence":"Testing has not started and the demo is tomorrow"}}

"The API integration is half done"
=> {{"task":"Complete API integration","owner":"Unassigned","deadline":"","priority":"Medium","status":"In Progress","evidence":"API integration is half done"}}

Transcript:
{transcript[:8000]}
"""
 
def extract_tasks(transcript: str) -> list:
    """
    Extract actionable tasks from transcript using LLM
    
    IMPROVEMENT: Now includes speaker context, so tasks
    are assigned to the correct person
    
    Args:
        transcript: Meeting transcript (now includes speakers)
        
    Returns:
        List of task dictionaries
    """
    try:
        logger.info("ðŸ“‹ Extracting tasks from transcript...")
        
        task_prompt = f"""
You are a Senior Project Manager.

Your job is to read meeting discussions and convert ALL unfinished work,
pending work, blockers, risks and commitments into actionable tasks.

IMPORTANT:

People rarely state tasks directly.

You MUST infer tasks from project status updates.

Examples:

"The SRS still needs work"
â†’ Finalize SRS

"The dashboard isn't complete"
â†’ Complete dashboard implementation

"Testing has not started"
â†’ Perform testing

"API integration remains pending"
â†’ Complete API integration

"Authentication module is only 70% done"
â†’ Complete authentication module

"We need support for gateway integration"
â†’ Complete gateway integration

"The UI still needs responsiveness checks"
â†’ Perform UI responsiveness testing

"Test cases should be prepared today"
â†’ Prepare integration test cases

"The backend issue may delay integration"
â†’ Resolve backend issue

"We should start execution runs tomorrow"
â†’ Start execution runs

--------------------------------------------------

Extract EVERY task you can identify.

For each task determine:

1. task
2. owner
3. deadline

Owner Rules:

If speaker explicitly commits:
"I will do it"
owner = speaker

Otherwise:
owner = "Unassigned"

Deadline Rules:

Extract if mentioned:

today
tomorrow
Friday
next week
this month

Otherwise:

deadline = ""

Return ONLY JSON.

Example:

[
  {{
    "task":"Finalize SRS",
    "owner":"Unassigned",
    "deadline":"this week"
  }},
  {{
    "task":"Complete authentication module",
    "owner":"Unassigned",
    "deadline":"Friday"
  }}
]

Transcript (shortened):
{transcript[:4000]}
"""   
        transcript = transcript.replace("[Unknown]", "")
        transcript = transcript.strip()
        task_prompt = build_task_extraction_prompt(transcript)
        response = call_llm_with_retries(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": task_prompt}],
            temperature=0.1,
            max_tokens=1600
        )
        
        message = response.choices[0].message

        content = getattr(message, "content", None)

        if content is None:
            logger.warning("LLM returned empty content (None)")
            return []
        print("\n===== TASK RESPONSE =====")
        print(content)
        print("=========================\n")
        if not content:
            logger.warning("No response from LLM")
            return []

        raw_output = content.strip()
        logger.info(f"Raw task output: {raw_output[:200]}...")
        
        # Clean markdown formatting
        raw_output = (
    raw_output
    .replace("```json", "")
    .replace("```", "")
    .strip()
)
        start = raw_output.find("[")
        end = raw_output.rfind("]") + 1

        if start != -1 and end != -1:
            raw_output = raw_output[start:end]
        
        # Parse JSON
        try:
            tasks = json.loads(raw_output)

        except Exception:

            logger.warning("JSON invalid, trying repair")

            start = raw_output.find("[")
            end = raw_output.rfind("]")

            if start != -1 and end != -1:
                try:
                    raw_output = raw_output[start:end+1]
                    tasks = json.loads(raw_output)
                except:
                    tasks = []
            else:
                tasks = []

        if isinstance(tasks, dict):
            tasks = [tasks]

        elif not isinstance(tasks, list):
            tasks = []

# Fallback if JSON parsing fails but task names exist
        if not tasks:

            import re

            matches = re.findall(
                r'"task"\s*:\s*"([^"]+)"',
            raw_output
    )

            tasks = []

            for m in matches:
                tasks.append({
            "task": m,
            "owner": "Unassigned",
            "deadline": "",
            "priority": "Medium"
        })

        cleaned_tasks = []
        for task in tasks:
            if not isinstance(task, dict):
                continue

            deadline = str(task.get("deadline", "") or "").strip()
            priority = str(task.get("priority", "") or "").strip().title()
            status = str(task.get("status", "") or "Pending").strip().title()

            if priority not in ["High", "Medium", "Low"]:
                priority = calculate_priority(deadline)
            if status not in ["Pending", "In Progress"]:
                status = "Pending"

            task["task"] = str(task.get("task", "") or "").strip()
            task["owner"] = normalize_owner(task.get("owner", ""))
            task["deadline"] = deadline
            task["priority"] = priority
            task["status"] = status
            task["evidence"] = str(task.get("evidence", "") or "").strip()

            if task["task"]:
                cleaned_tasks.append(task)

        tasks = cleaned_tasks

        logger.info(f"âœ… Extracted {len(tasks)} tasks")
        return tasks
    except Exception as e:

        logger.error(f"Task extraction failed: {e}")

        logger.warning(
        "Using transcript-based fallback extraction"
    )

        tasks = []

        keywords = [
        "need",
        "needs",
        "pending",
        "not done",
        "incomplete",
        "testing",
        "integration",
        "authentication",
        "srs",
        "review",
        "deploy"
    ]

        for line in transcript.split("\n"):

            if any(k in line.lower() for k in keywords):

                tasks.append({
                "task": line[:120],
                "owner": "Unassigned",
                "deadline": "",
                "priority": "Medium"
            })

        return tasks
 

# ============================================================================
# RISK DETECTION (NO CHANGES - works with new transcript)
# ============================================================================
 
def detect_risks(transcript: str) -> str:
    """
    Detect risks, blockers, and potential delays from transcript
    
    Args:
        transcript: Meeting transcript (with speaker info)
        
    Returns:
        Risk analysis text
    """
    try:
        logger.info("âš ï¸ Analyzing for risks and blockers...")
        
        risk_prompt = f"""
Analyze this meeting transcript for potential risks, blockers, and delays.
 
Provide 3-5 bullet points if issues found, otherwise say "No major risks identified."
 
Transcript (shortened):
{transcript}
"""
        
        response = call_llm_with_retries(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": risk_prompt}],
            max_tokens=500
        )
        
        risk_analysis = response.choices[0].message.content
        logger.info("âœ… Risk analysis complete")
        return risk_analysis
        
    except Exception as e:
        logger.error(f"Risk detection failed: {e}")
        return "Failed to analyze risks"
 
 
# ============================================================================
# DATABASE OPERATIONS (NO CHANGES - handles speaker info automatically)
# ============================================================================
 
def validate_task(task: dict) -> bool:
    """Validate task dictionary has required fields"""
    required_fields = ["task"]
    return all(task.get(field) for field in required_fields)
 
from datetime import datetime

def calculate_priority(deadline):

    if not deadline:
        return "Low"

    deadline = deadline.lower()

    high_keywords = [
        "today",
        "tomorrow",
        "urgent",
        "asap",
        "immediately"
    ]

    medium_keywords = [
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "this week"
    ]

    if any(word in deadline for word in high_keywords):
        return "High"

    if any(word in deadline for word in medium_keywords):
        return "Medium"

    return "Low"

def save_to_database(filename: str, transcript: str, summary: str, risk: str, tasks: list):
    """
    Save meeting and tasks to database with proper error handling
    
    Args:
        filename: Original audio filename
        transcript: Meeting transcript (with speakers)
        summary: Meeting summary
        risk: Risk analysis
        tasks: List of extracted tasks (with speaker owners)
        
    Returns:
        meeting_id if successful, None otherwise
    """
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Insert meeting
        logger.info("ðŸ’¾ Saving meeting to database...")
        cursor.execute("""
            INSERT INTO meetings (filename, transcript, summary, risk, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (
            os.path.basename(filename),
            transcript,
            summary,
            risk,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))
        
        meeting_id = cursor.lastrowid
        logger.info(f"âœ… Meeting saved (ID: {meeting_id})")
        
        # Insert tasks with meeting_id
        for task in tasks:
            if not validate_task(task):
                logger.warning(f"Skipping invalid task: {task}")
                continue
            
            try:
                cursor.execute("""
                SELECT COUNT(*) 
                FROM tasks
                WHERE task=?
                AND owner=?
                """,
                (
                task.get("task",""),
                task.get("owner","")
                ))
                exists = cursor.fetchone()[0]

                if exists:
                    continue
                cursor.execute("""
                    INSERT INTO tasks 
                    (meeting_id, task, owner, deadline, priority, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    meeting_id,
                    task.get("task", ""),
                    task.get("owner", "Unassigned"),
                    task.get("deadline", ""),
                    task.get("priority", "Medium"),
                    "Pending",
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                ))
                
                # Send alert for high priority tasks
                if task.get("priority") == "High":
                    logger.info(f"ðŸ”” Sending alert for high priority task: {task.get('task')}")
                    send_task_alert(task)
                    
            except sqlite3.Error as e:
                logger.error(f"Failed to insert task '{task.get('task')}': {e}")
                continue
        
        conn.commit()
        logger.info("âœ… All data saved to database")
        return meeting_id
        
    except sqlite3.Error as e:
        logger.error(f"Database error: {e}")
        if conn:
            conn.rollback()
        return None
        
    finally:
        if conn:
            conn.close()
 
 
def save_to_database(filename: str, transcript: str, summary: str, risk: str, tasks: list, file_hash: str = ""):
    """
    Save meeting output with meeting deduplication and canonical task updates.
    This definition intentionally overrides the legacy implementation above.
    """
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA journal_mode=MEMORY")
        cursor = conn.cursor()
        created_at = current_time()

        if file_hash:
            cursor.execute("SELECT id FROM meetings WHERE file_hash=? LIMIT 1", (file_hash,))
            existing_meeting = cursor.fetchone()
            if existing_meeting:
                return existing_meeting[0]

        cursor.execute("""
            INSERT INTO meetings (filename, transcript, summary, risk, created_at, last_updated, file_hash, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            os.path.basename(filename),
            transcript,
            summary,
            risk,
            created_at,
            created_at,
            file_hash,
            "Processed"
        ))

        meeting_id = cursor.lastrowid

        for task in tasks:
            if not validate_task(task):
                logger.warning(f"Skipping invalid task: {task}")
                continue

            owner = normalize_owner(task.get("owner", ""))
            key = task_key(task.get("task", ""), owner)
            priority = task.get("priority") or calculate_priority(task.get("deadline", ""))

            cursor.execute("""
                SELECT id
                FROM tasks
                WHERE task_key=?
                LIMIT 1
            """, (key,))
            existing_task = cursor.fetchone()

            if existing_task:
                cursor.execute("""
                    UPDATE tasks
                    SET deadline=COALESCE(NULLIF(?, ''), deadline),
                        priority=?,
                        last_updated=?,
                        duplicate_count=COALESCE(duplicate_count, 0) + 1
                    WHERE id=?
                """, (task.get("deadline", ""), priority, current_time(), existing_task[0]))
                continue

            cursor.execute("""
                INSERT INTO tasks
                (meeting_id, task, owner, deadline, priority, status, created_at, last_updated, task_key)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                meeting_id,
                task.get("task", ""),
                owner,
                task.get("deadline", ""),
                priority,
                "Pending",
                current_time(),
                current_time(),
                key
            ))

            if priority == "High":
                send_task_alert({**task, "owner": owner, "priority": priority})

        conn.commit()
        logger.info(f"Saved meeting {meeting_id} with {len(tasks)} extracted tasks")
        return meeting_id

    except sqlite3.Error as e:
        logger.error(f"Database error: {e}")
        if conn:
            conn.rollback()
        return None
    finally:
        if conn:
            conn.close()


# ============================================================================
# MAIN PROCESSING FUNCTION
# ============================================================================
 
def process_meeting(audio_path: str, job_id=None) -> tuple:
    """
    Main function to process a meeting recording end-to-end
    
    MAJOR CHANGES:
    1. WhisperX instead of basic Whisper
    2. Pyannote speaker diarization
    3. Speaker-aware task extraction
    4. Better logging and error handling
    
    Args:
        audio_path: Path to audio file
        
    Returns:
        Tuple of (transcript, summary, risk)
    """
    try:
        logger.info("="*70)
        logger.info(f"ðŸŽ¯ PROCESSING MEETING: {os.path.basename(audio_path)}")
        logger.info("="*70)
        
        meeting_hash = file_sha256(audio_path)
        update_processing_job(job_id, "Transcription", 10, message="Transcribing audio")

        # Step 1: Transcribe with speakers (MAJOR FIX: WhisperX + Pyannote)
        logger.info("\n[1/5] TRANSCRIPTION WITH SPEAKER DIARIZATION")
        result = transcribe_audio(audio_path)
        transcript = result['transcript']
        if not transcript.strip():
            raise ValueError("Transcript is empty. Please upload a clearer audio file.")
        logger.info(f"Language: {result['language']}")
        logger.info(f"Segments: {len(result['segments'])}")
        
        # Step 2: Generate summary
        update_processing_job(job_id, "Summary", 35, message="Generating summary")
        logger.info("\n[2/5] SUMMARY GENERATION")
        summary = extract_summary(transcript)
        
        # Step 3: Extract tasks (now with speaker context)
        # Step 3: Extract tasks
        update_processing_job(job_id, "Tasks", 55, message="Extracting action items")
        logger.info("\n[3/5] TASK EXTRACTION")
        tasks = extract_tasks(transcript)

# Fallback extraction if LLM returns nothing
        if not tasks:

            logger.warning(
        "LLM returned no tasks. Creating fallback tasks."
    )

            keywords = [
        "pending",
        "not done",
        "needs work",
        "need to",
        "incomplete",
        "testing",
        "integration",
        "authentication",
        "srs"
    ]

            tasks = []

            for line in transcript.split("\n"):

                if any(k in line.lower() for k in keywords):

                    tasks.append({
                "task": line[:100],
                "owner": "Unassigned",
                "deadline": "",
                "priority": "Medium"
            })

            logger.info(
        f"Fallback extracted {len(tasks)} tasks"
    )

        update_processing_job(job_id, "Risk Analysis", 70, message="Detecting risks and blockers")
# Step 4: Detect risks
        logger.info("\n[4/5] RISK ANALYSIS")
        risk = detect_risks(transcript)
        
        # Step 5: Generate report
        update_processing_job(job_id, "Report", 85, message="Generating report")
        logger.info("\n[5/5] REPORT GENERATION & STORAGE")
        if create_report(os.path.basename(audio_path), summary, risk):
            logger.info("âœ… Report generated")
        else:
            logger.warning("âš ï¸ Report generation failed, continuing...")
        
        # Step 6: Save to database
        update_processing_job(job_id, "Saving", 95, message="Saving meeting and tasks")
        meeting_id = save_to_database(audio_path, transcript, summary, risk, tasks, meeting_hash)
        update_processing_job(job_id, "Completed", 100, status="Completed", message="Processing complete", meeting_id=meeting_id)
        
        logger.info("\n" + "="*70)
        logger.info(f"âœ… PROCESSING COMPLETE")
        logger.info(f"   Meeting ID: {meeting_id}")
        logger.info(f"   Transcript: {len(transcript)} characters")
        logger.info(f"   Tasks: {len(tasks)}")
        logger.info(f"   Segments: {len(result['segments'])}")
        logger.info("="*70 + "\n")
        
        return (transcript, summary, risk)
        
    except Exception as e:
        logger.error(f"âŒ FATAL ERROR: {e}", exc_info=True)
        raise
 
 
# ============================================================================
# STARTUP STATUS
# ============================================================================
 
def print_startup_status():
    """Print available features on startup"""
    print("\n" + "="*70)
    print("AUDIO PROCESSING SETUP STATUS")
    print("="*70)
    print("OK  WhisperX:          Better transcription with word timestamps")
    print(f"{'OK' if HAS_PYANNOTE else 'WARN'} Pyannote:           Speaker diarization {'enabled' if HAS_PYANNOTE else 'disabled'}")
    print(f"{'OK' if HF_TOKEN else 'WARN'} Hugging Face Token: {'CONFIGURED' if HF_TOKEN else 'NOT SET'}")
    print("OK  OpenRouter API:    LLM for summary/tasks/risks")
    print("="*70 + "\n")
    return
    print("\n" + "="*70)
    print("ðŸŽµ AUDIO PROCESSING SETUP STATUS")
    print("="*70)
    print(f"âœ… WhisperX:               Better transcription with word timestamps")
    print(f"{'âœ…' if HAS_PYANNOTE else 'âš ï¸'} Pyannote:                Speaker diarization {'' if HAS_PYANNOTE else '(not installed)'}")
    print(f"{'âœ…' if HF_TOKEN else 'âš ï¸'} Hugging Face Token:      {'CONFIGURED' if HF_TOKEN else 'NOT SET'}")
    print(f"âœ… OpenRouter API:         LLM for summary/tasks/risks")
    print("="*70 + "\n")
 
if os.getenv("SHOW_PROCESSING_STARTUP_STATUS", "false").lower() == "true":
    print_startup_status()
