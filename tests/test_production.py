from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from vantera.config import Settings
from vantera.engine import Company
from vantera.production import export_public_state
from vantera.scheduler import Scheduler


class EmptyProvider:
    def discover(self):
        return []


class ProductionRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.company = Company(Settings(Path(self.temp.name) / "state.db"), EmptyProvider())
        self.company.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def test_remote_delivery_is_idempotent(self):
        first = Scheduler(self.company).run_remote("delivery-42")
        second = Scheduler(self.company).run_remote("delivery-42")
        self.assertIn("cycle_id", first)
        self.assertEqual("duplicate_delivery", second["skipped"])
        self.assertEqual(1, self.company.db.one("SELECT COUNT(*) count FROM job_runs")["count"])

    def test_authenticated_remote_delivery_can_force_a_real_cycle(self):
        self.company.db.initialize()
        result = Scheduler(self.company).run_remote("manual-proof", force=True)
        self.assertIn("cycle_id", result)
        run = self.company.db.one("SELECT status FROM job_runs WHERE run_key='manual-proof'")
        self.assertEqual("COMPLETED", run["status"])

    def test_export_is_valid_sanitized_atomic_state(self):
        Scheduler(self.company).run_once()
        output = Path(self.temp.name) / "public" / "data" / "state.json"
        state = export_public_state(self.company.db, output)
        self.assertEqual(0, state["policy"]["autonomous_spend_limit_cents"])
        self.assertEqual("AWAITING_REMOTE_AUTH", state["autonomy"]["status"])
        self.assertFalse(state["autonomy"]["remote_verified"])
        self.assertEqual(state, json.loads(output.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
