from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from vantera.config import Settings
from vantera.domain import Opportunity
from vantera.engine import BusinessUnitFactory, Company
from vantera.finance import FinancialLedger


class Provider:
    def discover(self):
        return [
            Opportunity("Free Tool", "Automated utility", "Creators", "Affiliate", "test",
                        signals={"demand": .9, "automation": 1, "distribution": .8, "margin": .8}),
            Opportunity("Inventory Store", "Stock products", "Consumers", "Retail", "test",
                        capital_required_cents=1000, signals={"demand": 1, "automation": 1}),
            Opportunity("Owner Consultancy", "Owner delivers calls", "SMBs", "Fee", "test",
                        human_operations_required=True, signals={"demand": 1, "automation": 1}),
        ]


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(Path(self.temp.name) / "test.db", auto_create_score=.75)
        self.company = Company(self.settings, Provider())
        self.company.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def test_cycle_executes_task_creates_unit_rejects_policy_failures_and_reports(self):
        result = self.company.cycle()
        self.assertEqual(1, len(result["venture"]["created"]))
        self.assertEqual(2, result["venture"]["rejected"])
        self.assertGreaterEqual(result["tasks"]["verified"], 1)
        self.assertGreaterEqual(result["tasks"]["blocked"], 1)
        self.assertEqual("NONE", result["report"]["owner_action_required"])
        self.assertEqual(0, result["report"]["money"]["total_verified_cash"])
        self.assertTrue(self.company.db.one("SELECT id FROM reports"))

    def test_factory_enforces_zero_capital_and_no_human_operations(self):
        factory = BusinessUnitFactory(self.company.db)
        for opportunity in (
            Opportunity("Paid", "x", "x", "x", "test", capital_required_cents=1),
            Opportunity("Human", "x", "x", "x", "test", human_operations_required=True),
        ):
            with self.assertRaises(ValueError):
                factory.create(opportunity)

    def test_financial_ledger_rejects_unverified_evidence(self):
        ledger = FinancialLedger(self.company.db)
        evidence = ledger.create_evidence("transaction", "test", {"amount": 10}, verified=False)
        with self.assertRaises(ValueError):
            ledger.record_verified(business_unit_id="bu_tiktok_affiliate", kind="REVENUE",
                                   amount_cents=1000, evidence_id=evidence)

    def test_financial_ledger_accepts_verified_evidence(self):
        ledger = FinancialLedger(self.company.db)
        evidence = ledger.create_evidence("transaction", "test", {"amount": 10}, verified=True)
        ledger.record_verified(business_unit_id="bu_tiktok_affiliate", kind="REVENUE",
                               amount_cents=1000, evidence_id=evidence)
        self.assertEqual(1000, ledger.totals()["total_verified_cash"])

    def test_scorecard_has_all_required_transparent_factors(self):
        score, rationale, card = self.company.ventures.score(Provider().discover()[0])
        expected = {"zero_capital_feasibility", "autonomy", "speed_to_launch",
                    "speed_to_first_revenue", "addressable_demand", "competition_attractiveness",
                    "distribution_accessibility", "technical_ease", "dependency_resilience", "scalability"}
        self.assertEqual(expected, set(card["factors"]))
        self.assertAlmostEqual(score, sum(card["contributions"].values()))

    def test_research_and_evidence_are_persisted(self):
        self.company.cycle()
        row = self.company.db.one("SELECT evidence_json,research_json,scorecard_json,decision FROM opportunity_research LIMIT 1")
        self.assertTrue(row)
        self.assertIsInstance(json.loads(row["scorecard_json"]), dict)

    def test_scheduler_persists_successful_job_state(self):
        from vantera.scheduler import Scheduler
        Scheduler(self.company).run_once()
        job = self.company.db.one("SELECT status,last_completed_at,attempts FROM jobs WHERE id='job_company_cycle'")
        self.assertEqual("IDLE", job["status"])
        self.assertEqual(1, job["attempts"])
        self.assertTrue(job["last_completed_at"])

    def test_repeat_cycle_deduplicates_opportunities_and_units(self):
        first = self.company.cycle()
        opportunity_count = self.company.db.one("SELECT COUNT(*) count FROM opportunities")["count"]
        unit_count = self.company.db.one("SELECT COUNT(*) count FROM business_units")["count"]
        second = self.company.cycle()
        self.assertEqual(0, second["venture"]["evaluated"])
        self.assertEqual(opportunity_count, self.company.db.one("SELECT COUNT(*) count FROM opportunities")["count"])
        self.assertEqual(unit_count, self.company.db.one("SELECT COUNT(*) count FROM business_units")["count"])


if __name__ == "__main__":
    unittest.main()
