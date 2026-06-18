from flask import Flask, render_template, request, redirect, send_file
from werkzeug.utils import secure_filename
import sqlite3
import os
import logging
from datetime import datetime
from processing import process_meeting
from workflow import meeting_graph
# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
 
app = Flask(__name__)
 
# Configuration
UPLOAD_FOLDER = "uploads"
ALLOWED_EXTENSIONS = {
    'mp3',
    'wav',
    'ogg',
    'm4a',
    'flac',
    'mp4'
}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
 
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
 
# Get debug mode from environment
DEBUG_MODE = os.getenv("FLASK_DEBUG", "False").lower() == "true"
 
 
def get_db_connection():
    """Create and return database connection"""
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row  # Access columns by name
    return conn
 
 
def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS
 
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
            SELECT id, task, owner, deadline, priority, status
            FROM tasks
            ORDER BY created_at DESC
            LIMIT 20
        """)
        tasks = cursor.fetchall()
        
        cursor.execute("SELECT COUNT(*) FROM meetings")
        total_meetings = cursor.fetchone()[0]
        
        return render_template(
            "dashboard.html",
            tasks=tasks,
            total_tasks=total_tasks,
            pending_tasks=pending_tasks,
            high_priority=high_priority,
            total_meetings=total_meetings,
            completed_tasks=completed_tasks
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
            SELECT id, filename, summary, risk, created_at
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
 
 
# ============================================================================
# COMPLETE TASK ROUTE
# ============================================================================
 
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
            "UPDATE tasks SET status='Completed' WHERE id=?",
            (task_id,)
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
        
        return render_template(
            "analytics.html",
            completed=completed,
            pending=pending,
            in_progress=in_progress,
            high=high,
            medium=medium,
            low=low,
            total=total,
            completion_rate=f"{completion_rate:.1f}%"
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
            SELECT id, filename, transcript, summary, risk, created_at
            FROM meetings
            WHERE id=?
        """, (meeting_id,))
        
        meeting = cursor.fetchone()
        
        if not meeting:
            logger.warning(f"Meeting not found: {meeting_id}")
            return render_template("error.html", error="Meeting not found"), 404
        
        # Get tasks for this meeting
        cursor.execute("""
            SELECT id, task, owner, deadline, priority, status, created_at
            FROM tasks
            WHERE meeting_id=?
            ORDER BY priority DESC, created_at DESC
        """, (meeting_id,))
        
        tasks = cursor.fetchall()
        
        logger.info(f"Loaded meeting detail: {meeting_id} with {len(tasks)} tasks")
        
        return render_template(
            "meeting_detail.html",
            meeting=meeting,
            tasks=tasks
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
 
 
# ============================================================================
# DOWNLOAD REPORT ROUTE
# ============================================================================
 
@app.route("/download/<filename>")
def download_report(filename):

    try:

        filename = secure_filename(filename)

        name = os.path.splitext(filename)[0]

        filepath = f"reports/{name}.pdf"

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
    app.run(
        debug=DEBUG_MODE,
        host="0.0.0.0",
        port=int(os.getenv("PORT", 5000))
    )