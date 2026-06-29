from flask import Flask, render_template, request, redirect, send_file, jsonify, url_for, session
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
import os
import logging
import threading
import time
import json
import re
import secrets
from datetime import datetime, timedelta
from schema import init_db
from agents import run_all_agents
from jobs import create_job_or_duplicate, run_processing_job
from drive_watcher import run_drive_check
from state import load_state
from storage import DB_PATH, UPLOAD_FOLDER, REPORTS_DIR, ensure_storage_dirs
from mailer import send_password_reset
# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
 
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", secrets.token_hex(32))
ensure_storage_dirs()
try:
    init_db()
except sqlite3.Error as exc:
    logger.warning(f"Database migration skipped during startup: {exc}")
 
# Configuration
ALLOWED_EXTENSIONS = {
    'mp3',
    'wav',
    'ogg',
    'm4a',
    'flac',
    'mp4',
    'txt'
}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
 
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
 
# Get debug mode from environment
DEBUG_MODE = os.getenv("FLASK_DEBUG", "False").lower() == "true"
_WATCHER_STARTED = False
 
 
def get_db_connection():
    """Create and return database connection"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=MEMORY")
    conn.row_factory = sqlite3.Row  # Access columns by name
    return conn
 
 
def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def auth_enabled():
    return os.getenv("AUTH_ENABLED", "true").lower() == "true"


def auth_time():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def user_count():
    conn = get_db_connection()
    count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    return count


def get_user_by_email(email):
    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE lower(email)=lower(?)", (email,)).fetchone()
    conn.close()
    return user


def get_user_by_id(user_id):
    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    return user


def create_user(name, email, password):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO users (name, email, password_hash, created_at)
        VALUES (?, ?, ?, ?)
    """, (name.strip(), email.strip().lower(), generate_password_hash(password), auth_time()))
    conn.commit()
    user_id = cursor.lastrowid
    conn.close()
    return user_id


def create_password_reset(user_id):
    token = secrets.token_urlsafe(32)
    conn = get_db_connection()
    conn.execute("""
        INSERT INTO password_resets (user_id, token, created_at, expires_at)
        VALUES (?, ?, ?, ?)
    """, (
        user_id,
        token,
        auth_time(),
        (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    ))
    conn.commit()
    conn.close()
    return token


def get_reset_by_token(token):
    conn = get_db_connection()
    reset = conn.execute("""
        SELECT pr.*, u.email
        FROM password_resets pr
        JOIN users u ON u.id=pr.user_id
        WHERE pr.token=?
          AND pr.used_at IS NULL
          AND pr.expires_at >= ?
    """, (token, auth_time())).fetchone()
    conn.close()
    return reset


def safe_next_url(value):
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return url_for("dashboard")


@app.before_request
def require_login():
    if not auth_enabled():
        return None

    public_endpoints = {"login", "register", "forgot_password", "reset_password", "static"}
    if request.endpoint in public_endpoints:
        return None

    if user_count() == 0:
        return redirect(url_for("register", next=request.full_path if request.query_string else request.path))

    if session.get("user_id") and get_user_by_id(session["user_id"]):
        return None

    return redirect(url_for("login", next=request.full_path if request.query_string else request.path))


@app.route("/login", methods=["GET", "POST"])
def login():
    if not auth_enabled():
        return redirect(url_for("dashboard"))

    if user_count() == 0:
        return redirect(url_for("register", next=request.args.get("next", "/")))

    next_url = safe_next_url(request.args.get("next"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = get_user_by_email(email)
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["user_email"] = user["email"]
            conn = get_db_connection()
            conn.execute("UPDATE users SET last_login_at=? WHERE id=?", (auth_time(), user["id"]))
            conn.commit()
            conn.close()
            return redirect(safe_next_url(request.form.get("next")))
        return render_template("login.html", error="Invalid password", next_url=next_url), 401

    return render_template("login.html", next_url=next_url)


@app.route("/register", methods=["GET", "POST"])
def register():
    if not auth_enabled():
        return redirect(url_for("dashboard"))

    registration_open = user_count() == 0 or os.getenv("ALLOW_REGISTRATION", "false").lower() == "true"
    if not registration_open:
        return redirect(url_for("login"))

    next_url = safe_next_url(request.args.get("next"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not name or not email or not password:
            return render_template("register.html", error="All fields are required.", next_url=next_url), 400
        if len(password) < 8:
            return render_template("register.html", error="Password must be at least 8 characters.", next_url=next_url), 400
        if password != confirm_password:
            return render_template("register.html", error="Passwords do not match.", next_url=next_url), 400
        if get_user_by_email(email):
            return render_template("register.html", error="An account already exists for this email.", next_url=next_url), 400

        user_id = create_user(name, email, password)
        session.clear()
        session["user_id"] = user_id
        session["user_email"] = email
        return redirect(safe_next_url(request.form.get("next")))

    return render_template("register.html", next_url=next_url)


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if not auth_enabled():
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = get_user_by_email(email)
        reset_link = None
        email_sent = False
        if user:
            token = create_password_reset(user["id"])
            reset_link = url_for("reset_password", token=token, _external=True)
            email_sent = send_password_reset(user["email"], reset_link)

        return render_template(
            "forgot_password.html",
            success="If that email exists, a reset link has been prepared.",
            reset_link=reset_link if not email_sent else None
        )

    return render_template("forgot_password.html")


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    if not auth_enabled():
        return redirect(url_for("dashboard"))

    reset = get_reset_by_token(token)
    if not reset:
        return render_template("reset_password.html", error="This reset link is invalid or expired."), 400

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        if len(password) < 8:
            return render_template("reset_password.html", token=token, error="Password must be at least 8 characters."), 400
        if password != confirm_password:
            return render_template("reset_password.html", token=token, error="Passwords do not match."), 400

        conn = get_db_connection()
        conn.execute(
            "UPDATE users SET password_hash=? WHERE id=?",
            (generate_password_hash(password), reset["user_id"])
        )
        conn.execute("UPDATE password_resets SET used_at=? WHERE id=?", (auth_time(), reset["id"]))
        conn.commit()
        conn.close()
        return redirect(url_for("login"))

    return render_template("reset_password.html", token=token)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


def current_time():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def extract_speaker_labels(transcript, tasks=None):
    labels = set(re.findall(r"\bSPEAKER[_-]?\d+\b", transcript or "", flags=re.IGNORECASE))

    for task in tasks or []:
        owner = task["owner"] if isinstance(task, sqlite3.Row) else task.get("owner", "")
        labels.update(re.findall(r"\bSPEAKER[_-]?\d+\b", owner or "", flags=re.IGNORECASE))

    normalized = []
    for label in labels:
        digits = re.findall(r"\d+", label)
        if digits:
            normalized.append(f"SPEAKER_{int(digits[0]):02d}")

    return sorted(set(normalized))


def load_speaker_map(raw_value):
    if not raw_value:
        return {}
    try:
        value = json.loads(raw_value)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def drive_watcher_loop():
    interval = int(os.getenv("DRIVE_WATCH_INTERVAL_SECONDS", "120"))
    while True:
        try:
            run_drive_check(async_process=True)
        except Exception as exc:
            logger.error(f"Drive watcher check failed: {exc}", exc_info=True)
        time.sleep(interval)


def start_drive_watcher_if_enabled():
    global _WATCHER_STARTED
    if _WATCHER_STARTED:
        return
    if os.getenv("DRIVE_WATCHER_ENABLED", "true").lower() != "true":
        return

    watcher = threading.Thread(target=drive_watcher_loop, daemon=True)
    watcher.start()
    _WATCHER_STARTED = True
    logger.info("Google Drive watcher started")


start_drive_watcher_if_enabled()
 
# ============================================================================
# DASHBOARD ROUTE
# ============================================================================
 
@app.route("/")
def dashboard():
    """Display dashboard with task statistics and recent tasks"""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get task statistics
        cursor.execute("SELECT COUNT(*) FROM tasks")
        total_tasks = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM tasks WHERE status='Pending'")
        pending_tasks = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM tasks WHERE priority='High'")
        high_priority = cursor.fetchone()[0]
        
        cursor.execute(
"SELECT COUNT(*) FROM tasks WHERE status='Completed'"
)

        completed_tasks = cursor.fetchone()[0]
        # Get recent tasks
        cursor.execute("""
            SELECT id, task, owner, deadline, priority, status, created_at, last_updated, duplicate_count, escalation_level
            FROM tasks
            ORDER BY last_updated DESC
            LIMIT 20
        """)
        tasks = cursor.fetchall()
        
        cursor.execute("SELECT COUNT(*) FROM meetings")
        total_meetings = cursor.fetchone()[0]
        drive_status = load_state().get("drive_status", {
            "status": "Not checked",
            "last_checked": "",
            "files_seen": 0,
            "files_queued": 0,
            "files_skipped": 0,
            "duplicates": 0,
            "last_error": ""
        })
        
        return render_template(
            "dashboard.html",
            tasks=tasks,
            total_tasks=total_tasks,
            pending_tasks=pending_tasks,
            high_priority=high_priority,
            total_meetings=total_meetings,
            completed_tasks=completed_tasks,
            drive_status=drive_status
        )
        
    except sqlite3.Error as e:
        logger.error(f"Database error in dashboard: {e}")
        return render_template("error.html", error="Database error"), 500
        
    finally:
        if conn:
            conn.close()
 
 
# ============================================================================
# UPLOAD PAGE ROUTE
# ============================================================================
 
@app.route("/upload")
def upload_page():
    """Display upload form"""
    return render_template("upload.html")
 
 
# ============================================================================
# MEETINGS LIST ROUTE
# ============================================================================
 
@app.route("/meetings")
def meetings():
    """Display list of all meetings"""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT id, filename, summary, risk, created_at, last_updated, status
            FROM meetings
            ORDER BY created_at DESC
            LIMIT 50
        """)
        
        meetings_data = cursor.fetchall()
        
        return render_template("meetings.html", meetings=meetings_data)
        
    except sqlite3.Error as e:
        logger.error(f"Database error in meetings: {e}")
        return render_template("error.html", error="Failed to load meetings"), 500
        
    finally:
        if conn:
            conn.close()
 
 
# ============================================================================
# PROCESS MEETING ROUTE
# ============================================================================
 
@app.route("/process", methods=["POST"])
def process():
    """Process uploaded audio file and extract meeting data"""
    
    # Validate file was provided
    if "audio" not in request.files:
        logger.warning("Upload attempt without file")
        return render_template("upload.html", error="No file uploaded"), 400
    
    file = request.files["audio"]
    
    # Validate filename
    if file.filename == "":
        logger.warning("Upload with empty filename")
        return render_template("upload.html", error="No file selected"), 400
    
    # Validate file extension
    if not allowed_file(file.filename):
        logger.warning(f"Invalid file type attempted: {file.filename}")
        error = f"Invalid file type. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"
        return render_template("upload.html", error=error), 400
    
    # Validate file size
    file.seek(0, 2)
    file_size = file.tell()
    file.seek(0)
    
    if file_size > MAX_FILE_SIZE:
        logger.warning(f"File too large: {file_size} bytes")
        return render_template(
            "upload.html",
            error=f"File too large (max {MAX_FILE_SIZE / (1024*1024):.0f}MB)"
        ), 400
    
    if file_size == 0:
        logger.warning("Empty file uploaded")
        return render_template("upload.html", error="File is empty"), 400
    
    try:
        # Sanitize filename and save
        filename = secure_filename(file.filename)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_")
        filename = timestamp + filename  # Prevent collisions
        
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        logger.info(f"Saving file: {filepath}")
        file.save(filepath)

        result = create_job_or_duplicate(filename, filepath, source="manual")
        duplicate_id = result["duplicate_meeting_id"]
        if duplicate_id:
            logger.info(f"Duplicate meeting upload detected: {filename}")
            return redirect(url_for("meeting_detail", meeting_id=duplicate_id))

        job_id = result["job_id"]
        worker = threading.Thread(target=run_processing_job, args=(job_id, filepath), daemon=True)
        worker.start()

        return redirect(url_for("processing_status", job_id=job_id))
        
        # Process meeting
        logger.info(f"Processing meeting: {filename}")
        result = meeting_graph.invoke(
    {
        "audio_path": filepath
    }
)
       
        transcript = result["transcript"]

        summary = result["summary"]

        risk = result["risk_text"]
        
        logger.info(f"✅ Meeting processed successfully: {filename}")
        
        return render_template(
            "upload.html",
            transcript=transcript,
            summary=summary,
            risk=risk,
            success="Meeting processed successfully!"
        )
        
    except ValueError as e:
        logger.error(f"Validation error: {e}")
        return render_template(
            "upload.html",
            error=f"Processing error: {str(e)}"
        ), 400
        
    except Exception as e:
        logger.error(f"Unexpected error in process: {e}", exc_info=True)
        return render_template(
            "upload.html",
            error="Failed to process meeting. Please try again."
        ), 500
 

@app.route("/processing/<int:job_id>")
def processing_status(job_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM processing_jobs WHERE id=?", (job_id,))
    job = cursor.fetchone()
    conn.close()

    if not job:
        return render_template("error.html", error="Processing job not found"), 404

    return render_template("processing_status.html", job=job)


@app.route("/api/jobs/<int:job_id>")
def processing_job_api(job_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM processing_jobs WHERE id=?", (job_id,))
    job = cursor.fetchone()
    conn.close()

    if not job:
        return jsonify({"error": "Job not found"}), 404

    data = dict(job)
    if data.get("meeting_id"):
        data["meeting_url"] = url_for("meeting_detail", meeting_id=data["meeting_id"])
    return jsonify(data)


@app.route("/agents/run", methods=["POST"])
def run_agents_route():
    results = run_all_agents()
    return redirect(url_for("dashboard", reminders=results["reminders"], escalations=results["escalations"], closures=results["closures"]))


@app.route("/drive/check", methods=["POST"])
def drive_check_route():
    try:
        results = run_drive_check(async_process=True)
        logger.info(f"Drive check completed with {len(results)} result(s)")
    except Exception as exc:
        logger.error(f"Drive check failed: {exc}", exc_info=True)
    return redirect(url_for("dashboard"))

 
# ============================================================================
# COMPLETE TASK ROUTE
# ============================================================================

VALID_TASK_STATUSES = {"Pending", "In Progress", "Completed"}


@app.route("/task/<int:task_id>/status", methods=["POST"])
def update_task_status(task_id):
    """Update task workflow status"""
    status = request.form.get("status", "").strip()
    if status not in VALID_TASK_STATUSES:
        logger.warning(f"Invalid task status attempted: {status}")
        return redirect("/")

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM tasks WHERE id=?", (task_id,))
        if not cursor.fetchone():
            logger.warning(f"Attempt to update non-existent task: {task_id}")
            return redirect("/")

        completed_at = current_time() if status == "Completed" else None
        cursor.execute(
            "UPDATE tasks SET status=?, completed_at=?, last_updated=? WHERE id=?",
            (status, completed_at, current_time(), task_id)
        )
        conn.commit()
        logger.info(f"Task {task_id} status updated to {status}")

    except sqlite3.Error as e:
        logger.error(f"Database error in update_task_status: {e}")
    finally:
        if conn:
            conn.close()

    return redirect("/")
 
@app.route("/complete/<int:task_id>", methods=["GET","POST"])
def complete_task(task_id):
    """Mark task as completed"""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Verify task exists
        cursor.execute("SELECT id FROM tasks WHERE id=?", (task_id,))
        if not cursor.fetchone():
            logger.warning(f"Attempt to complete non-existent task: {task_id}")
            return redirect("/")
        
        # Update status
        cursor.execute(
            "UPDATE tasks SET status='Completed', completed_at=?, last_updated=? WHERE id=?",
            (current_time(), current_time(), task_id)
        )
        
        conn.commit()
        logger.info(f"Task {task_id} marked as completed")
        
    except sqlite3.Error as e:
        logger.error(f"Database error in complete_task: {e}")
        
    finally:
        if conn:
            conn.close()
    
    return redirect("/")
 
 
# ============================================================================
# ANALYTICS ROUTE
# ============================================================================
 
@app.route("/analytics")
def analytics():
    """Display task analytics"""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM tasks WHERE status='Completed'")
        completed = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM tasks WHERE status='Pending'")
        pending = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM tasks WHERE status='In Progress'")
        in_progress = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM tasks WHERE priority='High'")
        high = cursor.fetchone()[0]
        
        cursor.execute(
"SELECT COUNT(*) FROM tasks WHERE priority='Medium'"
)
        medium = cursor.fetchone()[0]

        cursor.execute(
"SELECT COUNT(*) FROM tasks WHERE priority='Low'"
)
        low = cursor.fetchone()[0]
        
        total = completed + pending + in_progress
        completion_rate = (completed / total * 100) if total > 0 else 0

        priority_total = high + medium + low
        status_segments = {
            "completed": (completed / total * 100) if total else 0,
            "pending": (pending / total * 100) if total else 0,
            "in_progress": (in_progress / total * 100) if total else 0,
        }
        priority_segments = {
            "high": (high / priority_total * 100) if priority_total else 0,
            "medium": (medium / priority_total * 100) if priority_total else 0,
            "low": (low / priority_total * 100) if priority_total else 0,
        }

        cursor.execute("""
            SELECT COALESCE(NULLIF(owner, ''), 'Unassigned') AS owner, COUNT(*) AS count
            FROM tasks
            GROUP BY COALESCE(NULLIF(owner, ''), 'Unassigned')
            ORDER BY count DESC, owner ASC
            LIMIT 6
        """)
        owner_rows = cursor.fetchall()
        max_owner_count = max([row["count"] for row in owner_rows], default=0)
        owner_workload = [
            {
                "owner": row["owner"],
                "count": row["count"],
                "percent": (row["count"] / max_owner_count * 100) if max_owner_count else 0
            }
            for row in owner_rows
        ]
        
        return render_template(
            "analytics.html",
            completed=completed,
            pending=pending,
            in_progress=in_progress,
            high=high,
            medium=medium,
            low=low,
            total=total,
            completion_rate=f"{completion_rate:.1f}%",
            completion_rate_value=f"{completion_rate:.1f}",
            status_segments=status_segments,
            priority_segments=priority_segments,
            owner_workload=owner_workload
        )
        
    except sqlite3.Error as e:
        logger.error(f"Database error in analytics: {e}")
        return render_template("error.html", error="Failed to load analytics"), 500
        
    finally:
        if conn:
            conn.close()
 
 
# ============================================================================
# MEETING DETAIL ROUTE (FIXED)
# ============================================================================
 
@app.route("/meeting/<int:meeting_id>")
def meeting_detail(meeting_id):
    """Display detailed view of a specific meeting and its tasks"""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get meeting details
        cursor.execute("""
            SELECT id, filename, transcript, summary, risk, created_at, last_updated, status, speaker_map
            FROM meetings
            WHERE id=?
        """, (meeting_id,))
        
        meeting = cursor.fetchone()
        
        if not meeting:
            logger.warning(f"Meeting not found: {meeting_id}")
            return render_template("error.html", error="Meeting not found"), 404
        
        # Get tasks for this meeting
        cursor.execute("""
            SELECT id, task, owner, deadline, priority, status, created_at, last_updated, duplicate_count, escalation_level
            FROM tasks
            WHERE meeting_id=?
            ORDER BY priority DESC, last_updated DESC
        """, (meeting_id,))
        
        tasks = cursor.fetchall()
        speaker_map = load_speaker_map(meeting["speaker_map"])
        speaker_labels = extract_speaker_labels(meeting["transcript"], tasks)
        
        logger.info(f"Loaded meeting detail: {meeting_id} with {len(tasks)} tasks")
        
        return render_template(
            "meeting_detail.html",
            meeting=meeting,
            tasks=tasks,
            speaker_labels=speaker_labels,
            speaker_map=speaker_map
        )
        
    except sqlite3.Error as e:
        logger.error(f"Database error in meeting_detail: {e}")
        return render_template(
            "error.html",
            error="Failed to load meeting details"
        ), 500
        
    finally:
        if conn:
            conn.close()


@app.route("/meeting/<int:meeting_id>/speakers", methods=["POST"])
def update_speaker_names(meeting_id):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, transcript, speaker_map FROM meetings WHERE id=?", (meeting_id,))
        meeting = cursor.fetchone()

        if not meeting:
            return render_template("error.html", error="Meeting not found"), 404

        cursor.execute("SELECT owner FROM tasks WHERE meeting_id=?", (meeting_id,))
        task_rows = cursor.fetchall()
        speaker_labels = extract_speaker_labels(meeting["transcript"], task_rows)
        existing_map = load_speaker_map(meeting["speaker_map"])
        updated_map = dict(existing_map)

        for label in speaker_labels:
            name = request.form.get(f"speaker_{label}", "").strip()
            if name:
                updated_map[label] = name
                cursor.execute("""
                    UPDATE tasks
                    SET owner=?,
                        last_updated=?
                    WHERE meeting_id=?
                      AND (
                          owner=?
                          OR owner=?
                          OR owner=?
                      )
                """, (
                    name,
                    current_time(),
                    meeting_id,
                    label,
                    label.replace("_", " "),
                    f"[{label}]"
                ))

        cursor.execute("""
            UPDATE meetings
            SET speaker_map=?,
                last_updated=?
            WHERE id=?
        """, (json.dumps(updated_map), current_time(), meeting_id))
        conn.commit()
        logger.info(f"Updated speaker names for meeting {meeting_id}")

    except sqlite3.Error as e:
        logger.error(f"Database error in update_speaker_names: {e}")
    finally:
        if conn:
            conn.close()

    return redirect(url_for("meeting_detail", meeting_id=meeting_id))
 
 
# ============================================================================
# DOWNLOAD REPORT ROUTE
# ============================================================================
 
@app.route("/download/<filename>")
def download_report(filename):

    try:

        filename = secure_filename(filename)

        name = os.path.splitext(filename)[0]

        filepath = os.path.join(REPORTS_DIR, f"{name}.pdf")

        if not os.path.exists(filepath):
            return "File not found", 404

        return send_file(
            filepath,
            as_attachment=True,
            download_name=f"{name}.pdf"
        )

    except Exception as e:
        logger.error(f"Download error: {e}")
        return "Download failed", 500
# ============================================================================
# ERROR HANDLERS
# ============================================================================
 
@app.errorhandler(404)
def not_found(e):
    return "Page not found", 404
 
@app.errorhandler(500)
def server_error(e):
    return "Server error", 500
 
# ============================================================================
# MAIN
# ============================================================================
 
if __name__ == "__main__":
    logger.info(f"Starting Flask app (Debug: {DEBUG_MODE})")
    start_drive_watcher_if_enabled()

    app.run(
        debug=DEBUG_MODE,
        host="0.0.0.0",
        port=int(os.getenv("PORT", 5000))
    )
