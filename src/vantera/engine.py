from __future__ import annotations

import json
import uuid
from datetime import date
from typing import Any

from .agents import OpportunityProvider, executive_agents
from .config import Settings
from .db import Database, utcnow
from .domain import Opportunity
from .finance import FinancialLedger
from .tools import ToolRegistry


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class BusinessUnitFactory:
    def __init__(self, db: Database):
        self.db = db

    def create(self, opportunity: Opportunity, *, status: str = "VALIDATING") -> str:
        if opportunity.capital_required_cents > 0 or opportunity.human_operations_required:
            raise ValueError("Zero-capital and Owner non-involvement policies forbid this unit")
        unit_id = new_id("bu")
        now = utcnow()
        workers = ["market-researcher", "asset-builder", "distribution-operator", "analyst"]
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO business_units VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (unit_id, opportunity.name, opportunity.thesis, opportunity.target_customer,
                 opportunity.monetization_model, "coo", json.dumps(workers),
                 "verified conversions and revenue", status, None, 0, 0, now, now),
            )
        self.db.event("business_unit_created", "ceo", "EXECUTED", opportunity.to_dict(), "business_unit", unit_id)
        strategy = {"positioning": "Useful, transparent, source-linked resource",
                    "distribution": opportunity.research.get("distribution_method"),
                    "monetization": opportunity.monetization_model,
                    "path_to_revenue": opportunity.research.get("path_to_first_revenue")}
        with self.db.connect() as conn:
            conn.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                new_id("task"), unit_id, "Build sourced validation website",
                "Build the first real digital asset from retained public evidence.", "cto", "build_landing_page",
                json.dumps({"unit_id": unit_id, "name": opportunity.name, "thesis": opportunity.thesis,
                            "target_customer": opportunity.target_customer, "sources": opportunity.evidence,
                            "strategy": strategy}), "PLANNED", None, None, now, None, None))
            conn.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                new_id("task"), unit_id, "Publish validation website",
                "Publish through a configured free hosting adapter.", "cmo", "external_action",
                json.dumps({"unit_id": unit_id, "action": "publish_static_site"}),
                "PLANNED", None, None, now, None, None))
        return unit_id


class VentureEngine:
    def __init__(self, db: Database, provider: OpportunityProvider, settings: Settings):
        self.db, self.provider, self.settings = db, provider, settings
        self.factory = BusinessUnitFactory(db)

    @staticmethod
    def score(opportunity: Opportunity) -> tuple[float, str, dict[str, Any]]:
        if opportunity.capital_required_cents > 0:
            return 0.0, "Rejected: requires initial capital.", {"hard_gate": "initial_cash"}
        if opportunity.human_operations_required:
            return 0.0, "Rejected: requires human operational work.", {"hard_gate": "owner_operations"}
        signals = opportunity.signals
        if signals.get("demand", 0) < .04:
            return 0.0, "Rejected: current public evidence is too weak to justify autonomous execution.", {"hard_gate": "insufficient_demand_evidence"}
        factors = {
            "zero_capital_feasibility": 1.0,
            "autonomy": signals.get("automation", .5),
            "speed_to_launch": signals.get("speed_launch", .7),
            "speed_to_first_revenue": signals.get("speed_revenue", .4),
            "addressable_demand": signals.get("demand", .3),
            "competition_attractiveness": 1 - signals.get("competition", .5),
            "distribution_accessibility": signals.get("distribution", .5),
            "technical_ease": 1 - signals.get("technical_difficulty", .3),
            "dependency_resilience": 1 - signals.get("dependency_risk", .3),
            "scalability": signals.get("scalability", .7),
        }
        weights = {"zero_capital_feasibility": .15, "autonomy": .15, "speed_to_launch": .1,
                   "speed_to_first_revenue": .1, "addressable_demand": .15,
                   "competition_attractiveness": .05, "distribution_accessibility": .1,
                   "technical_ease": .07, "dependency_resilience": .06, "scalability": .07}
        contributions = {key: round(factors[key] * weights[key], 4) for key in factors}
        score = round(sum(contributions.values()), 4)
        return score, "Eligible for zero-capital autonomous validation.", {"factors": factors, "weights": weights, "contributions": contributions}

    def run(self) -> dict[str, Any]:
        discovered = self.provider.discover()
        created: list[str] = []
        rejected = 0
        active = self.db.one("SELECT COUNT(*) count FROM business_units WHERE status!='TERMINATED'")["count"]
        evaluated = []
        remaining_capacity = max(0, self.settings.max_units - active)
        for item in discovered:
            if self.db.one("SELECT id FROM opportunities WHERE name=? AND source=?", (item.name, item.source)):
                self.db.event("opportunity_duplicate_skipped", "cvo", "EXECUTED", {"name": item.name, "source": item.source})
                continue
            opportunity_id = new_id("opp")
            score, rationale, scorecard = self.score(item)
            if score == 0:
                decision = "REJECT"
            elif score >= self.settings.auto_create_score:
                decision = "BUILD"
            elif score >= self.settings.auto_create_score - .12:
                decision = "VALIDATE"
            else:
                decision = "WATCH" if score >= .45 else "REJECT"
            status = decision
            now = utcnow()
            with self.db.connect() as conn:
                conn.execute("INSERT INTO opportunities VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                    opportunity_id, item.name, item.thesis, item.target_customer, item.monetization_model,
                    item.source, item.capital_required_cents, int(item.human_operations_required), score,
                    status, rationale, now, now))
                conn.execute("INSERT INTO decisions VALUES(?,?,?,?,?,?,?)", (
                    new_id("dec"), "ceo", "OPPORTUNITY_EVALUATION", opportunity_id, status, rationale, now))
                conn.execute("INSERT INTO opportunity_research VALUES(?,?,?,?,?,?)", (
                    opportunity_id, json.dumps(item.evidence), json.dumps(item.research),
                    json.dumps(scorecard), decision, now))
            self.db.event("opportunity_evaluated", "cso", "EXECUTED",
                          {"score": score, "outcome": status, "rationale": rationale}, "opportunity", opportunity_id)
            evaluated.append((score, opportunity_id, item, decision, rationale))
            if decision == "REJECT": rejected += 1
        # CEO selects only the strongest BUILD candidate per cycle, preventing venture spam.
        candidates = [row for row in evaluated if row[3] == "BUILD"]
        if candidates and remaining_capacity:
            _, selected_id, selected, _, _ = max(candidates, key=lambda row: row[0])
            created.append(self.factory.create(selected))
            with self.db.connect() as conn:
                conn.execute("UPDATE opportunities SET status='BUILD' WHERE id=?", (selected_id,))
            for _, other_id, _, _, _ in candidates:
                if other_id != selected_id:
                    with self.db.connect() as conn:
                        conn.execute("UPDATE opportunities SET status='VALIDATE', rationale=rationale || ' Deferred: stronger candidate selected this cycle.' WHERE id=?", (other_id,))
                        conn.execute("UPDATE opportunity_research SET decision='VALIDATE' WHERE opportunity_id=?", (other_id,))
                        conn.execute("UPDATE decisions SET outcome='VALIDATE', rationale=rationale || ' Deferred: stronger candidate selected this cycle.' WHERE subject_id=?", (other_id,))
            evaluated = [(s, oid, item, "VALIDATE" if d == "BUILD" and oid != selected_id else d, r)
                         for s, oid, item, d, r in evaluated]
        return {"discovered": len(discovered), "evaluated": len(evaluated), "created": created, "rejected": rejected,
                "decisions": [{"id": oid, "name": item.name, "score": score, "decision": decision,
                               "evidence": item.evidence, "rationale": rationale}
                              for score, oid, item, decision, rationale in evaluated]}


class TaskExecutor:
    def __init__(self, db: Database, tools: ToolRegistry):
        self.db, self.tools = db, tools

    def execute_pending(self) -> dict[str, int]:
        tasks = self.db.query("SELECT * FROM tasks WHERE status='PLANNED' ORDER BY created_at LIMIT ?", (20,))
        counts = {"executed": 0, "verified": 0, "blocked": 0}
        for task in tasks:
            tool = self.tools.get(task["action_type"])
            if not tool:
                self._finish(task["id"], "BLOCKED", {"summary": "Unknown action tool"})
                counts["blocked"] += 1
                continue
            with self.db.connect() as conn:
                conn.execute("UPDATE tasks SET status='EXECUTING',started_at=? WHERE id=?", (utcnow(), task["id"]))
            result = tool.execute(json.loads(task["payload_json"]))
            evidence_id = None
            if result.evidence:
                evidence_id = FinancialLedger(self.db).create_evidence(
                    result.evidence["kind"], result.evidence["source"], result.evidence["payload"],
                    verified=result.verified)
            status = "VERIFIED" if result.executed and result.verified else "EXECUTED" if result.executed else "BLOCKED"
            self._finish(task["id"], status, {"summary": result.summary, "data": result.data}, evidence_id)
            counts[status.lower()] += 1
            self.db.event("task_completed", task["assigned_agent"],
                          "VERIFIED RESULT" if status == "VERIFIED" else "EXECUTED",
                          {"status": status, "summary": result.summary}, "task", task["id"])
        return counts

    def _finish(self, task_id: str, status: str, result: dict[str, Any], evidence_id: str | None = None) -> None:
        with self.db.connect() as conn:
            conn.execute("UPDATE tasks SET status=?,result_json=?,evidence_id=?,completed_at=? WHERE id=?",
                         (status, json.dumps(result), evidence_id, utcnow(), task_id))


class CEOReport:
    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def euros(cents: int) -> str:
        return f"€{cents / 100:,.2f}"

    def generate(self) -> dict[str, Any]:
        report_date = date.today().isoformat()
        money = FinancialLedger(self.db).totals()
        active = self.db.query("SELECT id,name,status,revenue_cents,expense_cents FROM business_units WHERE status!='TERMINATED'")
        new_count = self.db.one("SELECT COUNT(*) count FROM business_units WHERE substr(created_at,1,10)=? AND id!='bu_tiktok_affiliate'", (report_date,))["count"]
        terminated = self.db.one("SELECT COUNT(*) count FROM business_units WHERE status='TERMINATED' AND substr(updated_at,1,10)=?", (report_date,))["count"]
        experiments = self.db.one("SELECT COUNT(*) count FROM experiments WHERE status='RUNNING'")["count"]
        actions = self.db.query("SELECT event_type,actor,phase,payload_json FROM events WHERE substr(occurred_at,1,10)=? AND phase!='PLANNED' ORDER BY id DESC LIMIT 10", (report_date,))
        pipeline = self.db.query("SELECT name,status,score FROM opportunities WHERE status IN ('WATCH','VALIDATE','BUILD') ORDER BY score DESC LIMIT 5")
        winners = sorted(active, key=lambda x: x["revenue_cents"] - x["expense_cents"], reverse=True)[:3]
        failures = self.db.query("SELECT name,thesis FROM business_units WHERE status='TERMINATED' AND substr(updated_at,1,10)=?", (report_date,))
        launched = self.db.query("SELECT name,status FROM business_units WHERE launch_date=?", (report_date,))
        blocked = self.db.query("SELECT title,result_json FROM tasks WHERE status='BLOCKED' ORDER BY completed_at DESC LIMIT 5")
        next_tasks = self.db.query("SELECT title,assigned_agent,status FROM tasks WHERE status IN ('PLANNED','BLOCKED') ORDER BY created_at LIMIT 5")
        data = {"date": report_date, "money": money, "active_units": active, "new_units": new_count,
                "terminated_units": terminated, "experiments_running": experiments, "actions": actions,
                "winners": winners, "failures": failures, "launched": launched, "blocked": blocked,
                "next_tasks": next_tasks, "pipeline": pipeline, "owner_action_required": "NONE"}
        action_lines = "\n".join(f"- {a['actor']}: {a['event_type']} [{a['phase']}]" for a in actions) or "- No executed actions recorded."
        winner_lines = "\n".join(f"- {u['name']}: {self.euros(u['revenue_cents'] - u['expense_cents'])} verified net" for u in winners) or "- None yet."
        failure_lines = "\n".join(f"- {u['name']}: {u['thesis']}" for u in failures) or "- None today."
        pipeline_lines = "\n".join(f"- {o['name']} ({o['status']}, score {o['score']:.2f})" for o in pipeline) or "- No externally sourced opportunities awaiting action."
        launched_lines = "\n".join(f"- {u['name']}" for u in launched) or "- None today."
        next_lines = "\n".join(f"- {t['assigned_agent']}: {t['title']} [{t['status']}]" for t in next_tasks) or "- Continue evidence-backed discovery."
        body = f"""VANTERA DAILY EXECUTIVE REPORT

Date: {report_date}

REAL MONEY
Revenue today: {self.euros(money['revenue_today'])}
Revenue MTD: {self.euros(money['revenue_mtd'])}
Expenses today: {self.euros(money['expenses_today'])}
Profit today: {self.euros(money['profit_today'])}
Total verified cash generated: {self.euros(money['total_verified_cash'])}

PORTFOLIO
Active business units: {len(active)}
New business units: {new_count}
Units terminated: {terminated}
Experiments running: {experiments}

NEW VENTURES
{new_count}

VENTURES LAUNCHED
{launched_lines}

VENTURES TERMINATED
{terminated}

WHAT VANTERA ACTUALLY DID TODAY
{action_lines}

WINNERS
{winner_lines}

FAILURES
{failure_lines}

VENTURE PIPELINE
{pipeline_lines}

WHAT VANTERA WILL DO NEXT
{next_lines}

CEO COMMENT
The autonomous cycle completed using only configured capabilities.
External outcomes remain unclaimed unless independently verified.
Zero-capital and Owner non-involvement policies remain enforced.

OWNER ACTION REQUIRED
NONE
"""
        with self.db.connect() as conn:
            conn.execute("INSERT INTO reports VALUES(?,?,?,?,?) ON CONFLICT(report_date) DO UPDATE SET body=excluded.body,data_json=excluded.data_json,created_at=excluded.created_at",
                         (new_id("rpt"), report_date, body, json.dumps(data), utcnow()))
        self.db.event("daily_report_generated", "ceo", "EXECUTED", {"date": report_date}, "report", report_date)
        return {"body": body, "data": data}


class Company:
    def __init__(self, settings: Settings, provider: OpportunityProvider, tools: ToolRegistry | None = None):
        self.settings, self.db = settings, Database(settings.database_path)
        self.ventures = VentureEngine(self.db, provider, settings)
        self.tasks = TaskExecutor(self.db, tools or ToolRegistry(settings.database_path.parent / "ventures"))

    def initialize(self) -> None:
        self.db.initialize()
        now = utcnow()
        with self.db.connect() as conn:
            for agent in executive_agents():
                conn.execute("INSERT OR IGNORE INTO agents VALUES(?,?,?,?,?,?,?)",
                             (agent.id, agent.name, agent.role, agent.reports_to, "ACTIVE", "{}", now))
            conn.execute("INSERT OR REPLACE INTO company_state VALUES('owner_role',?,?)", ("Executive Chairman (non-operational)", now))
        self.seed_tiktok_unit()

    def seed_tiktok_unit(self) -> None:
        if self.db.one("SELECT id FROM business_units WHERE id='bu_tiktok_affiliate'"):
            return
        now = utcnow()
        with self.db.connect() as conn:
            conn.execute("INSERT INTO business_units VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                "bu_tiktok_affiliate", "VANTERA TikTok Shop Affiliate Unit",
                "Test whether autonomous organic short-form product discovery can generate affiliate conversions at zero capital.",
                "TikTok shoppers", "Affiliate commission", "cmo",
                json.dumps(["trend-researcher", "script-writer", "performance-analyst"]),
                "verified affiliate conversions and revenue", "VALIDATING", None, 0, 0, now, now))
            conn.execute("INSERT INTO experiments VALUES(?,?,?,?,?,?,?,?,?,?)", (
                "exp_tiktok_account_readiness", "bu_tiktok_affiliate", "Account and API readiness",
                "The unit can launch when a compliant TikTok Shop account and publishing adapter are configured.",
                "configured adapters", 2, "PLANNED", None, now, None))
        self.db.event("business_unit_seeded", "ceo", "EXECUTED", {"demo": False, "external_activity": False}, "business_unit", "bu_tiktok_affiliate")

    def cycle(self) -> dict[str, Any]:
        cycle_id = new_id("cycle")
        self.db.event("cycle_started", "ceo", "EXECUTED", {"cycle_id": cycle_id})
        venture = self.ventures.run()
        tasks = self.tasks.execute_pending()
        report = CEOReport(self.db).generate()
        result = {"cycle_id": cycle_id, "venture": venture, "tasks": tasks, "report": report["data"]}
        self.db.event("cycle_completed", "ceo", "VERIFIED RESULT", result)
        return result
