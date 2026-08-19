from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS company_state (
  key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agents (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL, reports_to TEXT,
  status TEXT NOT NULL DEFAULT 'ACTIVE', config_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS business_units (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, thesis TEXT NOT NULL, target_customer TEXT NOT NULL,
  monetization_model TEXT NOT NULL, responsible_executive TEXT NOT NULL, workers_json TEXT NOT NULL,
  kpi TEXT NOT NULL, status TEXT NOT NULL, launch_date TEXT, revenue_cents INTEGER NOT NULL DEFAULT 0,
  expense_cents INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS opportunities (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, thesis TEXT NOT NULL, target_customer TEXT NOT NULL,
  monetization_model TEXT NOT NULL, source TEXT NOT NULL, capital_required_cents INTEGER NOT NULL,
  human_operations_required INTEGER NOT NULL, score REAL, status TEXT NOT NULL,
  rationale TEXT, created_at TEXT NOT NULL, evaluated_at TEXT
);
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY, business_unit_id TEXT, title TEXT NOT NULL, description TEXT NOT NULL,
  assigned_agent TEXT NOT NULL, action_type TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL, result_json TEXT, evidence_id TEXT, created_at TEXT NOT NULL,
  started_at TEXT, completed_at TEXT,
  FOREIGN KEY(business_unit_id) REFERENCES business_units(id)
);
CREATE TABLE IF NOT EXISTS experiments (
  id TEXT PRIMARY KEY, business_unit_id TEXT NOT NULL, name TEXT NOT NULL, hypothesis TEXT NOT NULL,
  metric TEXT NOT NULL, target REAL NOT NULL, status TEXT NOT NULL, result REAL,
  started_at TEXT NOT NULL, ended_at TEXT, FOREIGN KEY(business_unit_id) REFERENCES business_units(id)
);
CREATE TABLE IF NOT EXISTS evidence (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, source TEXT NOT NULL, external_reference TEXT,
  payload_json TEXT NOT NULL, verified INTEGER NOT NULL DEFAULT 0, verified_at TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS financial_records (
  id TEXT PRIMARY KEY, business_unit_id TEXT, kind TEXT NOT NULL, amount_cents INTEGER NOT NULL,
  currency TEXT NOT NULL DEFAULT 'EUR', evidence_id TEXT NOT NULL, occurred_at TEXT NOT NULL,
  created_at TEXT NOT NULL, FOREIGN KEY(business_unit_id) REFERENCES business_units(id),
  FOREIGN KEY(evidence_id) REFERENCES evidence(id)
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT NOT NULL, actor TEXT NOT NULL,
  entity_type TEXT, entity_id TEXT, phase TEXT NOT NULL, payload_json TEXT NOT NULL,
  occurred_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions (
  id TEXT PRIMARY KEY, actor TEXT NOT NULL, decision_type TEXT NOT NULL, subject_id TEXT,
  outcome TEXT NOT NULL, rationale TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reports (
  id TEXT PRIMARY KEY, report_date TEXT NOT NULL UNIQUE, body TEXT NOT NULL,
  data_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS opportunity_research (
  opportunity_id TEXT PRIMARY KEY, evidence_json TEXT NOT NULL, research_json TEXT NOT NULL,
  scorecard_json TEXT NOT NULL, decision TEXT NOT NULL, created_at TEXT NOT NULL,
  FOREIGN KEY(opportunity_id) REFERENCES opportunities(id)
);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
  last_started_at TEXT, last_completed_at TEXT, next_run_at TEXT, last_error TEXT, state_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS execution_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, level TEXT NOT NULL, message TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}', occurred_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS job_runs (
  run_key TEXT PRIMARY KEY, job_id TEXT NOT NULL, status TEXT NOT NULL,
  started_at TEXT NOT NULL, completed_at TEXT, result_json TEXT,
  FOREIGN KEY(job_id) REFERENCES jobs(id)
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_events_time ON events(occurred_at);
CREATE INDEX IF NOT EXISTS idx_financial_time ON financial_records(occurred_at);
"""


class AutoClosingConnection(sqlite3.Connection):
    """Commit/rollback like sqlite3.Connection, then reliably release the file."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, factory=AutoClosingConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def initialize(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            self._migrate(conn)
            conn.execute("INSERT OR IGNORE INTO jobs(id,name,status,state_json) VALUES('job_company_cycle','company_cycle','IDLE','{}')")

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Apply additive, repeatable migrations to databases created by older releases."""
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
        additions = {
            "lease_owner": "TEXT",
            "lease_expires_at": "TEXT",
            "consecutive_failures": "INTEGER NOT NULL DEFAULT 0",
            "daily_runs": "INTEGER NOT NULL DEFAULT 0",
            "daily_run_date": "TEXT",
        }
        for name, declaration in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {declaration}")
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES(1,?)",
            (utcnow(),),
        )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def event(self, event_type: str, actor: str, phase: str, payload: dict[str, Any],
              entity_type: str | None = None, entity_id: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO events(event_type,actor,entity_type,entity_id,phase,payload_json,occurred_at) VALUES(?,?,?,?,?,?,?)",
                (event_type, actor, entity_type, entity_id, phase, json.dumps(payload), utcnow()),
            )
