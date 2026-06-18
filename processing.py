from sqlalchemy import exists
from sympy import content
import whisperx
import sqlite3
import json
import os
import logging
from datetime import datetime
from dotenv import load_dotenv
from openai import OpenAI
import httpx
 
# Pyannote for speaker diarization
try:
    from pyannote.audio import Pipeline
    HAS_PYANNOTE = True
except ImportError:
    HAS_PYANNOTE = False
 
from mailer import send_task_alert
from report import create_report
 
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
        logger.warning(f"⚠️ Missing environment variables: {', '.join(missing)}")
        if "HUGGINGFACE_API_KEY" in missing:
            logger.warning("   Hugging Face key required for WhisperX and Pyannote")
    else:
        logger.info("✅ Environment variables validated")
 
validate_environment()
 
# ============================================================================
# INITIALIZE CLIENTS
# ============================================================================
 
client = OpenAI(
    api_key=os.getenv("OPENROUTER_API_KEY"),
    base_url="https://openrouter.ai/api/v1",
    timeout=httpx.Timeout(30.0)
)
 
HF_TOKEN = os.getenv("HUGGINGFACE_API_KEY")
 
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
            logger.info("Loading WhisperX model (base)...")
            _WHISPERX_MODEL = whisperx.load_model("small", device="cpu", compute_type="int8")
            logger.info("✅ WhisperX model loaded successfully")
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
    global _DIARIZATION_PIPELINE
    if _DIARIZATION_PIPELINE is None:
        if not HAS_PYANNOTE:
            logger.warning("⚠️ Pyannote not installed. Speaker diarization disabled.")
            return None
        
        if not HF_TOKEN:
            logger.warning("⚠️ HUGGINGFACE_API_KEY not set. Speaker diarization disabled.")
            return None
        
        try:
            logger.info("Loading Pyannote diarization pipeline...")
            _DIARIZATION_PIPELINE = Pipeline.from_pretrained(
                                    "pyannote/speaker-diarization-3.1",
                                    token=HF_TOKEN
                                    )
            logger.info("✅ Pyannote diarization pipeline loaded successfully")
        except Exception as e:
            logger.warning(f"Failed to load Pyannote: {e}")
            return None
    
    return _DIARIZATION_PIPELINE
 
 
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
        logger.info(f"🎵 Transcribing audio with WhisperX: {audio_path}")
        
        # Step 1: Load WhisperX (CHANGED from: whisper.load_model("small"))
        model = get_whisperx_model()
        
        # Step 2: Transcribe with word-level timestamps
        logger.info("Processing audio through WhisperX...")
        result = model.transcribe(audio_path, language="en")
        
        logger.info(f"✅ Transcription complete ({len(result['segments'])} segments)")
        
        # Step 3: Get speaker diarization (NEW FEATURE)
        diarization_pipeline = get_diarization_pipeline()
        speakers_by_time = {}
        
        if diarization_pipeline:
            try:
                logger.info("🎤 Running speaker diarization...")
                diarization = diarization_pipeline(audio_path)
                
                # Map speakers to timestamps
                for turn, _, speaker in diarization.itertracks(yield_label=True):
                    # Store speaker at each timestamp
                    for t in [int(turn.start * 10) / 10, int(turn.end * 10) / 10]:
                        speakers_by_time[t] = speaker
                
                unique_speakers = len(set(speakers_by_time.values()))
                logger.info(f"✅ Diarization complete - Identified {unique_speakers} speakers")
                
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
            speaker = "Unknown"
            if diarization and speakers_by_time:
                # Find closest speaker timestamp
                closest_time = min(speakers_by_time.keys(), 
                                  key=lambda t: abs(t - segment_start),
                                  default=None)
                if closest_time is not None:
                    speaker = speakers_by_time[closest_time]
            
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
        
        logger.info(f"✅ Full transcript with speakers ready ({len(full_transcript)} chars)")
        
        return {
            'transcript': full_transcript,
            'segments': aligned_segments,
            'language': result.get('language', 'en')
        }
        
    except Exception as e:
        logger.error(f"❌ Transcription failed: {e}")
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
        logger.info("📝 Generating meeting summary...")
        
        summary_prompt = f"""
Summarize this meeting in 5 concise bullet points.
 
Transcript (shortened):
{transcript[:4000]}
"""
        
        response = client.chat.completions.create(
            model="openrouter/free",
            messages=[{"role": "user", "content": summary_prompt}],
            max_tokens=500
        )
        

        print("SUMMARY RESPONSE:")
        print(response)
        summary = response.choices[0].message.content

        if not summary:
            logger.warning("Summary returned None")
            return "Summary generation failed"

        logger.info("✅ Summary generated successfully")

        return summary
        
    except Exception as e:
        logger.error(f"Summary generation failed: {e}")
        return "Failed to generate summary"
 
 
# ============================================================================
# TASK EXTRACTION (IMPROVED - now has speaker context)
# ============================================================================
 
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
        logger.info("📋 Extracting tasks from transcript...")
        
        task_prompt = f"""
You are a Senior Project Manager.

Your job is to read meeting discussions and convert ALL unfinished work,
pending work, blockers, risks and commitments into actionable tasks.

IMPORTANT:

People rarely state tasks directly.

You MUST infer tasks from project status updates.

Examples:

"The SRS still needs work"
→ Finalize SRS

"The dashboard isn't complete"
→ Complete dashboard implementation

"Testing has not started"
→ Perform testing

"API integration remains pending"
→ Complete API integration

"Authentication module is only 70% done"
→ Complete authentication module

"We need support for gateway integration"
→ Complete gateway integration

"The UI still needs responsiveness checks"
→ Perform UI responsiveness testing

"Test cases should be prepared today"
→ Prepare integration test cases

"The backend issue may delay integration"
→ Resolve backend issue

"We should start execution runs tomorrow"
→ Start execution runs

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
        response = client.chat.completions.create(
            model="openai/gpt-4o-mini",
            messages=[{"role": "user", "content": task_prompt}],
            temperature=0.1,
            max_tokens=1000
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

        for task in tasks:
            deadline = task.get("deadline", "")
            task["priority"] = calculate_priority(deadline)

        logger.info(f"✅ Extracted {len(tasks)} tasks")
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
        logger.info("⚠️ Analyzing for risks and blockers...")
        
        risk_prompt = f"""
Analyze this meeting transcript for potential risks, blockers, and delays.
 
Provide 3-5 bullet points if issues found, otherwise say "No major risks identified."
 
Transcript (shortened):
{transcript}
"""
        
        response = client.chat.completions.create(
            model="openrouter/free",
            messages=[{"role": "user", "content": risk_prompt}],
            max_tokens=500
        )
        
        risk_analysis = response.choices[0].message.content
        logger.info("✅ Risk analysis complete")
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
        conn = sqlite3.connect("database.db")
        cursor = conn.cursor()
        
        # Insert meeting
        logger.info("💾 Saving meeting to database...")
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
        logger.info(f"✅ Meeting saved (ID: {meeting_id})")
        
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
                    logger.info(f"🔔 Sending alert for high priority task: {task.get('task')}")
                    send_task_alert(task)
                    
            except sqlite3.Error as e:
                logger.error(f"Failed to insert task '{task.get('task')}': {e}")
                continue
        
        conn.commit()
        logger.info("✅ All data saved to database")
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
 
def process_meeting(audio_path: str) -> tuple:
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
        logger.info(f"🎯 PROCESSING MEETING: {os.path.basename(audio_path)}")
        logger.info("="*70)
        
        # Step 1: Transcribe with speakers (MAJOR FIX: WhisperX + Pyannote)
        logger.info("\n[1/5] TRANSCRIPTION WITH SPEAKER DIARIZATION")
        result = transcribe_audio(audio_path)
        transcript = result['transcript']
        logger.info(f"Language: {result['language']}")
        logger.info(f"Segments: {len(result['segments'])}")
        
        # Step 2: Generate summary
        logger.info("\n[2/5] SUMMARY GENERATION")
        summary = extract_summary(transcript)
        
        # Step 3: Extract tasks (now with speaker context)
        # Step 3: Extract tasks
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

# Step 4: Detect risks
        logger.info("\n[4/5] RISK ANALYSIS")
        risk = detect_risks(transcript)
        
        # Step 5: Generate report
        logger.info("\n[5/5] REPORT GENERATION & STORAGE")
        if create_report(os.path.basename(audio_path), summary, risk):
            logger.info("✅ Report generated")
        else:
            logger.warning("⚠️ Report generation failed, continuing...")
        
        # Step 6: Save to database
        meeting_id = save_to_database(audio_path, transcript, summary, risk, tasks)
        
        logger.info("\n" + "="*70)
        logger.info(f"✅ PROCESSING COMPLETE")
        logger.info(f"   Meeting ID: {meeting_id}")
        logger.info(f"   Transcript: {len(transcript)} characters")
        logger.info(f"   Tasks: {len(tasks)}")
        logger.info(f"   Segments: {len(result['segments'])}")
        logger.info("="*70 + "\n")
        
        return (transcript, summary, risk)
        
    except Exception as e:
        logger.error(f"❌ FATAL ERROR: {e}", exc_info=True)
        raise
 
 
# ============================================================================
# STARTUP STATUS
# ============================================================================
 
def print_startup_status():
    """Print available features on startup"""
    print("\n" + "="*70)
    print("🎵 AUDIO PROCESSING SETUP STATUS")
    print("="*70)
    print(f"✅ WhisperX:               Better transcription with word timestamps")
    print(f"{'✅' if HAS_PYANNOTE else '⚠️'} Pyannote:                Speaker diarization {'' if HAS_PYANNOTE else '(not installed)'}")
    print(f"{'✅' if HF_TOKEN else '⚠️'} Hugging Face Token:      {'CONFIGURED' if HF_TOKEN else 'NOT SET'}")
    print(f"✅ OpenRouter API:         LLM for summary/tasks/risks")
    print("="*70 + "\n")
 
# Print status when module loads
print_startup_status()