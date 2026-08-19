from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from vantera.config import Settings
from vantera.engine import Company
from vantera.remote_jobs import RemoteWorkload


class EmptyProvider:
    def discover(self):
        return []


class RemoteWorkloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.company = Company(Settings(Path(self.temp.name) / "state.db"), EmptyProvider())
        self.company.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def test_execution_workload_is_durable_and_idempotent(self):
        workload = RemoteWorkload(self.company, "execution")
        first = workload.run_remote("delivery-one", force=True)
        duplicate = workload.run_remote("delivery-one", force=True)
        job = self.company.db.one("SELECT * FROM jobs WHERE id='job_execution'")
        self.assertIn("verified", first)
        self.assertEqual("duplicate_delivery", duplicate["skipped"])
        self.assertEqual("IDLE", job["status"])
        self.assertTrue(job["last_completed_at"])
        self.assertTrue(job["next_run_at"])

    def test_execution_workload_skips_early_delivery(self):
        workload = RemoteWorkload(self.company, "execution")
        workload.run_remote("delivery-one", force=True)
        result = workload.run_remote("delivery-two")
        self.assertEqual("not_due", result["skipped"])
        self.assertEqual(
            1,
            self.company.db.one("SELECT attempts FROM jobs WHERE id='job_execution'")["attempts"],
        )

    def test_reporting_workload_generates_persisted_report(self):
        result = RemoteWorkload(self.company, "reporting").run_remote("report-one", force=True)
        self.assertEqual(result["report_date"], self.company.db.one("SELECT report_date FROM reports")["report_date"])

    def test_failure_is_persisted_with_backoff_and_releases_lease(self):
        workload = RemoteWorkload(self.company, "execution")

        def fail():
            raise RuntimeError("executor unavailable")

        self.company.tasks.execute_pending = fail
        with self.assertRaisesRegex(RuntimeError, "executor unavailable"):
            workload.run_remote("failed-delivery", force=True)
        job = self.company.db.one("SELECT * FROM jobs WHERE id='job_execution'")
        run = self.company.db.one("SELECT * FROM job_runs WHERE run_key='failed-delivery'")
        self.assertEqual("RETRY", job["status"])
        self.assertEqual(1, job["consecutive_failures"])
        self.assertTrue(job["next_run_at"])
        self.assertIsNone(job["lease_owner"])
        self.assertEqual("FAILED", run["status"])


if __name__ == "__main__":
    unittest.main()
