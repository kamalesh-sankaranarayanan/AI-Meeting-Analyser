import importlib
import io
import os
import shutil
import sqlite3
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class AppFlowTests(unittest.TestCase):
    def setUp(self):
        self.original_cwd = os.getcwd()
        self.tmpdir = ROOT / ".test_runs" / uuid.uuid4().hex
        self.tmpdir.mkdir(parents=True, exist_ok=True)
        os.chdir(self.tmpdir)
        os.environ["DRIVE_WATCHER_ENABLED"] = "false"
        os.environ["AUTH_ENABLED"] = "false"
        os.environ.pop("APP_PASSWORD", None)

        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))

        for name in [
            "app",
            "schema",
            "jobs",
            "agents",
            "drive_watcher",
            "storage",
            "workflow",
            "processing",
        ]:
            sys.modules.pop(name, None)

        self.app_module = importlib.import_module("app")
        self.client = self.app_module.app.test_client()

    def tearDown(self):
        for name in [
            "app",
            "schema",
            "jobs",
            "agents",
            "drive_watcher",
            "storage",
            "workflow",
            "processing",
        ]:
            sys.modules.pop(name, None)
        os.chdir(self.original_cwd)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def seed_task(self):
        conn = sqlite3.connect("database.db")
        conn.execute("PRAGMA journal_mode=MEMORY")
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO meetings (filename, transcript, summary, risk, created_at, last_updated, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, ("demo.wav", "Transcript", "Summary", "Risk", "2026-06-20 10:00:00", "2026-06-20 10:00:00", "Processed"))
        meeting_id = cursor.lastrowid
        cursor.execute("""
            INSERT INTO tasks (meeting_id, task, owner, deadline, priority, status, created_at, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (meeting_id, "Prepare SRS", "Unassigned", "Friday", "Medium", "Pending", "2026-06-20 10:00:00", "2026-06-20 10:00:00"))
        task_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return task_id

    def seed_speaker_task(self):
        conn = sqlite3.connect("database.db")
        conn.execute("PRAGMA journal_mode=MEMORY")
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO meetings (filename, transcript, summary, risk, created_at, last_updated, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, ("speakers.wav", "[SPEAKER_00] I will prepare SRS", "Summary", "Risk", "2026-06-20 10:00:00", "2026-06-20 10:00:00", "Processed"))
        meeting_id = cursor.lastrowid
        cursor.execute("""
            INSERT INTO tasks (meeting_id, task, owner, deadline, priority, status, created_at, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (meeting_id, "Prepare SRS", "SPEAKER_00", "Friday", "Medium", "Pending", "2026-06-20 10:00:00", "2026-06-20 10:00:00"))
        conn.commit()
        conn.close()
        return meeting_id

    def test_core_pages_render(self):
        self.seed_task()

        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/upload").status_code, 200)
        self.assertEqual(self.client.get("/meetings").status_code, 200)
        self.assertEqual(self.client.get("/analytics").status_code, 200)

    def test_upload_requires_file(self):
        response = self.client.post("/process", data={}, content_type="multipart/form-data")

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"No file uploaded", response.data)

    def test_upload_rejects_invalid_extension(self):
        response = self.client.post(
            "/process",
            data={"audio": (io.BytesIO(b"hello"), "notes.exe")},
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Invalid file type", response.data)

    def test_upload_duplicate_redirects_to_existing_meeting(self):
        with patch.object(self.app_module, "create_job_or_duplicate", return_value={"duplicate_meeting_id": 7, "job_id": None}):
            response = self.client.post(
                "/process",
                data={"audio": (io.BytesIO(b"fake audio"), "meeting.wav")},
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/meeting/7"))

    def test_account_auth_register_login_and_reset(self):
        os.environ["AUTH_ENABLED"] = "true"

        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/register", response.headers["Location"])

        response = self.client.post(
            "/register",
            data={
                "name": "Demo User",
                "email": "demo@example.com",
                "password": "demo-pass-123",
                "confirm_password": "demo-pass-123",
                "next": "/",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/"))

        self.client.post("/logout")
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

        response = self.client.post(
            "/login",
            data={"email": "demo@example.com", "password": "demo-pass-123", "next": "/"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)

        self.client.post("/logout")
        response = self.client.post("/forgot-password", data={"email": "demo@example.com"})
        self.assertEqual(response.status_code, 200)

        conn = sqlite3.connect("database.db")
        token = conn.execute("SELECT token FROM password_resets ORDER BY id DESC LIMIT 1").fetchone()[0]
        conn.close()

        response = self.client.post(
            f"/reset-password/{token}",
            data={"password": "new-pass-123", "confirm_password": "new-pass-123"},
        )
        self.assertEqual(response.status_code, 302)

        response = self.client.post(
            "/login",
            data={"email": "demo@example.com", "password": "new-pass-123", "next": "/"},
        )
        self.assertEqual(response.status_code, 302)

    def test_upload_queues_processing_job(self):
        with patch.object(self.app_module, "create_job_or_duplicate", return_value={"duplicate_meeting_id": None, "job_id": 42}), \
             patch.object(self.app_module, "run_processing_job", return_value=None):
            response = self.client.post(
                "/process",
                data={"audio": (io.BytesIO(b"fake audio"), "meeting.wav")},
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/processing/42"))

    def test_complete_task_updates_status(self):
        task_id = self.seed_task()

        response = self.client.post(f"/complete/{task_id}")

        self.assertEqual(response.status_code, 302)
        conn = sqlite3.connect("database.db")
        status, completed_at = conn.execute(
            "SELECT status, completed_at FROM tasks WHERE id=?",
            (task_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(status, "Completed")
        self.assertIsNotNone(completed_at)

    def test_task_status_can_move_to_in_progress(self):
        task_id = self.seed_task()

        response = self.client.post(
            f"/task/{task_id}/status",
            data={"status": "In Progress"},
        )

        self.assertEqual(response.status_code, 302)
        conn = sqlite3.connect("database.db")
        status, completed_at = conn.execute(
            "SELECT status, completed_at FROM tasks WHERE id=?",
            (task_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(status, "In Progress")
        self.assertIsNone(completed_at)

    def test_speaker_mapping_updates_task_owner(self):
        meeting_id = self.seed_speaker_task()

        response = self.client.post(
            f"/meeting/{meeting_id}/speakers",
            data={"speaker_SPEAKER_00": "Rahul"},
        )

        self.assertEqual(response.status_code, 302)
        conn = sqlite3.connect("database.db")
        owner, speaker_map = conn.execute(
            "SELECT t.owner, m.speaker_map FROM tasks t JOIN meetings m ON m.id=t.meeting_id WHERE m.id=?",
            (meeting_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(owner, "Rahul")
        self.assertIn("Rahul", speaker_map)

    def test_pronoun_owner_normalizes_to_unassigned(self):
        processing = importlib.import_module("processing")

        self.assertEqual(processing.normalize_owner("I"), "Unassigned")
        self.assertEqual(processing.normalize_owner("we"), "Unassigned")
        self.assertEqual(processing.normalize_owner("Unknown"), "Unassigned")
        self.assertEqual(processing.normalize_owner("Rahul"), "Rahul")


if __name__ == "__main__":
    unittest.main()
