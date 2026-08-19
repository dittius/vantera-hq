from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from vantera.config import Settings
from vantera.db import Database
from vantera.engine import Company
from vantera.scheduler import AlreadyRunning, Scheduler
from vantera.web import Dashboard


class EmptyProvider:
    def discover(self):
        return []


class ProductionRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "production.db"
        self.company = Company(Settings(self.path, cycle_interval_seconds=600), EmptyProvider())
        self.company.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def test_initialize_migrates_an_existing_jobs_table_idempotently(self):
        legacy = Path(self.temp.name) / "legacy.db"
        conn = sqlite3.connect(legacy)
        conn.execute(
            "CREATE TABLE jobs (id TEXT PRIMARY KEY,name TEXT UNIQUE,status TEXT,attempts INTEGER DEFAULT 0,"
            "last_started_at TEXT,last_completed_at TEXT,next_run_at TEXT,last_error TEXT,state_json TEXT DEFAULT '{}')"
        )
        conn.execute("INSERT INTO jobs(id,name,status) VALUES('job_company_cycle','company_cycle','IDLE')")
        conn.commit()
        conn.close()

        db = Database(legacy)
        db.initialize()
        db.initialize()
        columns = {row["name"] for row in db.query("PRAGMA table_info(jobs)")}
        self.assertTrue({"lease_owner", "lease_expires_at", "consecutive_failures", "daily_runs"} <= columns)
        self.assertEqual(1, db.one("SELECT COUNT(*) count FROM schema_migrations")["count"])

    def test_persistent_lease_prevents_concurrent_remote_cycle(self):
        scheduler = Scheduler(self.company)
        scheduler._acquire("runner-one")
        with self.assertRaises(AlreadyRunning):
            Scheduler(self.company)._acquire("runner-two")
        job = self.company.db.one("SELECT lease_owner,status FROM jobs WHERE id='job_company_cycle'")
        self.assertEqual("runner-one", job["lease_owner"])
        self.assertEqual("RUNNING", job["status"])
        scheduler._release("runner-one")

    def test_expired_lease_is_recovered(self):
        expired = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        with self.company.db.connect() as conn:
            conn.execute(
                "UPDATE jobs SET lease_owner='dead-runner',lease_expires_at=? WHERE id='job_company_cycle'",
                (expired,),
            )
        Scheduler(self.company)._acquire("recovery-runner")
        job = self.company.db.one("SELECT lease_owner FROM jobs WHERE id='job_company_cycle'")
        self.assertEqual("recovery-runner", job["lease_owner"])

    def test_cron_entrypoint_is_idempotent_until_next_run(self):
        scheduler = Scheduler(self.company)
        first = scheduler.run_if_due()
        self.assertIsNotNone(first)
        attempts = self.company.db.one("SELECT attempts FROM jobs WHERE id='job_company_cycle'")["attempts"]
        second = scheduler.run_if_due()
        self.assertIsNone(second)
        self.assertEqual(attempts, self.company.db.one("SELECT attempts FROM jobs WHERE id='job_company_cycle'")["attempts"])

    def test_daily_budget_blocks_runaway_invocations(self):
        today = datetime.now(UTC).date().isoformat()
        with self.company.db.connect() as conn:
            conn.execute(
                "UPDATE jobs SET daily_runs=24,daily_run_date=? WHERE id='job_company_cycle'",
                (today,),
            )
        with self.assertRaisesRegex(RuntimeError, "budget exhausted"):
            Scheduler(self.company).run_once()

    def test_api_state_contains_only_persisted_company_state(self):
        app = Dashboard(self.company.db)
        captured = {}

        def start_response(status, headers):
            captured["status"] = status
            captured["headers"] = dict(headers)

        body = b"".join(app({"PATH_INFO": "/api/state"}, start_response))
        payload = json.loads(body)
        self.assertEqual("200 OK", captured["status"])
        self.assertEqual(0, payload["money"]["verified_revenue_cents"])
        self.assertTrue(any(unit["id"] == "bu_tiktok_affiliate" for unit in payload["units"]))
        self.assertNotIn("projected_revenue", payload)

    def test_api_maps_actual_executor_status_to_live_worker(self):
        now = datetime.now(UTC).isoformat()
        with self.company.db.connect() as conn:
            conn.execute(
                "INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("task_live", "bu_tiktok_affiliate", "Compile asset", "Real task", "cto", "code",
                 "{}", "EXECUTING", None, None, now, now, None),
            )
        payload = Dashboard(self.company.db).state()
        cto = next(worker for worker in payload["workers"] if worker["id"] == "cto")
        self.assertEqual("CODING", cto["state"])
        self.assertEqual("task_live", cto["task"]["id"])
        self.assertEqual(["Compile asset"], payload["autonomy"]["currently_executing"])


if __name__ == "__main__":
    unittest.main()
