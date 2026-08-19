from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
import uuid

from .db import utcnow


class AlreadyRunning(RuntimeError):
    pass


class Scheduler:
    def __init__(self, company):
        self.company = company
        self.lease_seconds = max(300, min(company.settings.cycle_interval_seconds, 3600))
        self.max_daily_runs = 24

    def _acquire(self, owner: str) -> None:
        now = datetime.now(UTC)
        now_text = now.isoformat()
        expires = (now + timedelta(seconds=self.lease_seconds)).isoformat()
        today = now.date().isoformat()
        with self.company.db.transaction() as conn:
            job = conn.execute("SELECT * FROM jobs WHERE id='job_company_cycle'").fetchone()
            if not job:
                raise RuntimeError("company_cycle job is not initialized")
            runs = job["daily_runs"] if job["daily_run_date"] == today else 0
            if runs >= self.max_daily_runs:
                raise RuntimeError("Daily autonomous cycle budget exhausted")
            if job["lease_expires_at"] and job["lease_expires_at"] > now_text:
                raise AlreadyRunning("A company cycle holds the persistent lease")
            changed = conn.execute(
                "UPDATE jobs SET lease_owner=?,lease_expires_at=?,status='RUNNING',"
                "attempts=attempts+1,last_started_at=?,last_error=NULL,daily_runs=?,daily_run_date=? "
                "WHERE id='job_company_cycle' AND (lease_expires_at IS NULL OR lease_expires_at<=?)",
                (owner, expires, now_text, runs + 1, today, now_text),
            ).rowcount
            if changed != 1:
                raise AlreadyRunning("Company cycle lease acquisition lost a race")

    def _release(self, owner: str) -> None:
        with self.company.db.connect() as conn:
            conn.execute(
                "UPDATE jobs SET lease_owner=NULL,lease_expires_at=NULL WHERE id='job_company_cycle' AND lease_owner=?",
                (owner,),
            )

    def run_once(self):
        db = self.company.db
        owner = uuid.uuid4().hex
        self._acquire(owner)
        now = utcnow()
        with db.connect() as conn:
            conn.execute("INSERT INTO execution_logs(job_id,level,message,payload_json,occurred_at) VALUES('job_company_cycle','INFO','Cycle started',?,?)", (json.dumps({"lease_owner": owner}), now))
        try:
            try:
                result = self.company.cycle()
            except Exception as exc:
                job = db.one("SELECT consecutive_failures FROM jobs WHERE id='job_company_cycle'")
                failures = int(job["consecutive_failures"] or 0) + 1
                delay_minutes = min(360, 15 * (2 ** min(failures - 1, 5)))
                retry_at = (datetime.now(UTC) + timedelta(minutes=delay_minutes)).isoformat()
                with db.connect() as conn:
                    conn.execute("UPDATE jobs SET status='RETRY',next_run_at=?,last_error=?,consecutive_failures=? WHERE id='job_company_cycle'", (retry_at, str(exc)[:1000], failures))
                    conn.execute("INSERT INTO execution_logs(job_id,level,message,payload_json,occurred_at) VALUES('job_company_cycle','ERROR','Cycle failed',?,?)", (json.dumps({"error": str(exc)}), utcnow()))
                raise
            next_run = (datetime.now(UTC) + timedelta(seconds=self.company.settings.cycle_interval_seconds)).isoformat()
            with db.connect() as conn:
                conn.execute("UPDATE jobs SET status='IDLE',last_completed_at=?,next_run_at=?,last_error=NULL,consecutive_failures=0 WHERE id='job_company_cycle'", (utcnow(), next_run))
                conn.execute("INSERT INTO execution_logs(job_id,level,message,payload_json,occurred_at) VALUES('job_company_cycle','INFO','Cycle completed',?,?)", (json.dumps({"cycle_id": result['cycle_id']}), utcnow()))
            return result
        finally:
            self._release(owner)

    def run_if_due(self):
        """Idempotent entry point for cron/serverless invocations."""
        job = self.company.db.one("SELECT next_run_at FROM jobs WHERE id='job_company_cycle'")
        if job and job["next_run_at"] and job["next_run_at"] > utcnow():
            return None
        return self.run_once()

    def run_remote(self, run_key: str, *, force: bool = False):
        """Idempotent durable entry point for retryable cloud-scheduler deliveries."""
        if not run_key:
            raise ValueError("A stable remote run key is required")
        now = utcnow()
        with self.company.db.transaction() as conn:
            prior = conn.execute("SELECT status,result_json FROM job_runs WHERE run_key=?", (run_key,)).fetchone()
            if prior:
                return {"skipped": "duplicate_delivery", "status": prior["status"],
                        "result": json.loads(prior["result_json"]) if prior["result_json"] else None}
            conn.execute("INSERT INTO job_runs(run_key,job_id,status,started_at) VALUES(?,'job_company_cycle','ACCEPTED',?)", (run_key, now))
        try:
            result = self.run_once() if force else self.run_if_due()
            outcome = {"skipped": "not_due"} if result is None else result
            with self.company.db.connect() as conn:
                conn.execute("UPDATE job_runs SET status='COMPLETED',completed_at=?,result_json=? WHERE run_key=?",
                             (utcnow(), json.dumps(outcome), run_key))
            return outcome
        except Exception:
            with self.company.db.connect() as conn:
                conn.execute("UPDATE job_runs SET status='FAILED',completed_at=? WHERE run_key=?", (utcnow(), run_key))
            raise

    def daemon(self, max_cycles: int | None = None):
        completed = 0
        while max_cycles is None or completed < max_cycles:
            job = self.company.db.one("SELECT next_run_at FROM jobs WHERE id='job_company_cycle'")
            if not job.get("next_run_at") or job["next_run_at"] <= utcnow():
                try:
                    self.run_if_due()
                except AlreadyRunning:
                    pass
                except Exception:
                    time.sleep(min(900, self.company.settings.cycle_interval_seconds))
                completed += 1
            time.sleep(min(60, self.company.settings.cycle_interval_seconds))
