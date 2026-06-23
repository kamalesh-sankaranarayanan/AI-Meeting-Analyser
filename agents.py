import re
import sqlite3
from datetime import datetime, timedelta

from mailer import send_alert


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _parse_due_at(deadline, created_at):
    if not deadline:
        return None

    base = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
    text = deadline.strip().lower()

    if "today" in text:
        return base.replace(hour=23, minute=59, second=59)
    if "tomorrow" in text:
        return (base + timedelta(days=1)).replace(hour=23, minute=59, second=59)
    if "this week" in text:
        return (base + timedelta(days=7)).replace(hour=23, minute=59, second=59)
    if "next week" in text:
        return (base + timedelta(days=14)).replace(hour=23, minute=59, second=59)

    weekdays = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    for name, index in weekdays.items():
        if name in text:
            days = (index - base.weekday()) % 7
            days = days or 7
            return (base + timedelta(days=days)).replace(hour=23, minute=59, second=59)

    return None


def run_followup_reminder_agent():
    conn = sqlite3.connect("database.db")
    conn.execute("PRAGMA journal_mode=MEMORY")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, task, owner, deadline, priority, created_at
        FROM tasks
        WHERE status != 'Completed'
          AND reminder_sent_at IS NULL
    """)
    sent = 0
    now = datetime.now()

    for task in cursor.fetchall():
        due_at = _parse_due_at(task["deadline"], task["created_at"])
        should_remind = task["priority"] == "High" or (due_at and due_at - now <= timedelta(days=1))
        if not should_remind:
            continue

        send_alert(
            f"Task reminder: {task['task']}",
            f"Owner: {task['owner'] or 'Unassigned'}<br>Deadline: {task['deadline'] or 'Not set'}"
        )
        cursor.execute("UPDATE tasks SET reminder_sent_at=?, last_updated=? WHERE id=?", (now_text(), now_text(), task["id"]))
        sent += 1

    conn.commit()
    conn.close()
    return sent


def run_escalation_agent():
    conn = sqlite3.connect("database.db")
    conn.execute("PRAGMA journal_mode=MEMORY")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, task, owner, deadline, priority, created_at, escalation_level
        FROM tasks
        WHERE status != 'Completed'
    """)
    escalated = 0
    now = datetime.now()

    for task in cursor.fetchall():
        due_at = _parse_due_at(task["deadline"], task["created_at"])
        is_overdue = due_at and due_at < now
        stale_high = task["priority"] == "High" and datetime.strptime(task["created_at"], "%Y-%m-%d %H:%M:%S") < now - timedelta(days=2)

        if not (is_overdue or stale_high):
            continue

        level = (task["escalation_level"] or 0) + 1
        cursor.execute("""
            UPDATE tasks
            SET priority='High',
                escalated_at=?,
                escalation_level=?,
                last_updated=?
            WHERE id=?
        """, (now_text(), level, now_text(), task["id"]))
        escalated += 1

    conn.commit()
    conn.close()
    return escalated


def run_closure_detection_agent():
    conn = sqlite3.connect("database.db")
    conn.execute("PRAGMA journal_mode=MEMORY")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        SELECT t.id, t.task, m.summary, m.transcript
        FROM tasks t
        JOIN meetings m ON m.id=t.meeting_id
        WHERE t.status != 'Completed'
    """)
    candidates = 0
    done_words = re.compile(r"\b(done|completed|finished|closed|resolved|delivered)\b", re.I)

    for row in cursor.fetchall():
        haystack = f"{row['summary'] or ''}\n{row['transcript'] or ''}"
        task_words = [word for word in re.findall(r"[a-z0-9]+", row["task"].lower()) if len(word) > 4]
        overlap = sum(1 for word in task_words[:6] if word in haystack.lower())

        if done_words.search(haystack) and overlap >= 2:
            candidates += 1

    conn.close()
    return candidates


def run_all_agents():
    return {
        "reminders": run_followup_reminder_agent(),
        "escalations": run_escalation_agent(),
        "closures": run_closure_detection_agent(),
    }
