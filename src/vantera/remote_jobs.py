from __future__ import annotations

import argparse
import json
import os
import uuid
from datetime import UTC, datetime, timedelta

from .cli import build_company
from .db import utcnow
from .engine import CEOReport
from .scheduler import AlreadyRunning


JOB_POLICY = {
    "execution": {"interval": 45 * 60, "daily_limit": 32},
    "reporting": {"interval": 20 * 60 * 60, "daily_limit": 2},
}


class RemoteWorkload:
    """Durable, independently scheduled work that does not perform discovery."""

    def __init__(self, company, workload: str):
        if workload not in JOB_POLICY:
            raise ValueError(f"Unknown workload: {workload}")
        self.company = company
        self.workload = workload
        self.job_id = f"job_{workload}"
        self.policy = JOB_POLICY[workload]
        with company.db.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO jobs(id,name,status,state_json) VALUES(?,?, 'IDLE','{}')",
                (self.job_id, workload),
            )

    def _acquire(self, owner: str) -> None:
        now = datetime.now(UTC)
        now_text = now.isoformat()
        today = now.date().isoformat()
        expires = (now + timedelta(minutes=20)).isoformat()
        with self.company.db.transaction() as conn:
            job = conn.execute("SELECT * FROM jobs WHERE id=?", (self.job_id,)).fetchone()
            runs = job["daily_runs"] if job["daily_run_date"] == today else 0
            if runs >= self.policy["daily_limit"]:
                raise RuntimeError(f"Daily {self.workload} budget exhausted")
            if job["lease_expires_at"] and job["lease_expires_at"] > now_text:
                raise AlreadyRunning(f"{self.workload} holds the persistent lease")
            changed = conn.execute(
                "UPDATE jobs SET lease_owner=?,lease_expires_at=?,status='RUNNING',attempts=attempts+1,"
                "last_started_at=?,last_error=NULL,daily_runs=?,daily_run_date=? WHERE id=? "
                "AND (lease_expires_at IS NULL OR lease_expires_at<=?)",
                (owner, expires, now_text, runs + 1, today, self.job_id, now_text),
            ).rowcount
            if changed != 1:
                raise AlreadyRunning(f"Lost the {self.workload} lease race")

    def _release(self, owner: str) -> None:
        with self.company.db.connect() as conn:
            conn.execute(
                "UPDATE jobs SET lease_owner=NULL,lease_expires_at=NULL WHERE id=? AND lease_owner=?",
                (self.job_id, owner),
            )

    def run_remote(self, run_key: str, *, force: bool = False) -> dict:
        if not run_key:
            raise ValueError("A stable remote run key is required")
        db = self.company.db
        now = utcnow()
        with db.transaction() as conn:
            prior = conn.execute("SELECT status,result_json FROM job_runs WHERE run_key=?", (run_key,)).fetchone()
            if prior:
                return {"skipped": "duplicate_delivery", "status": prior["status"]}
            conn.execute(
                "INSERT INTO job_runs(run_key,job_id,status,started_at) VALUES(?,?, 'ACCEPTED',?)",
                (run_key, self.job_id, now),
            )
        job = db.one("SELECT next_run_at FROM jobs WHERE id=?", (self.job_id,))
        if not force and job.get("next_run_at") and job["next_run_at"] > now:
            result = {"skipped": "not_due"}
            with db.connect() as conn:
                conn.execute(
                    "UPDATE job_runs SET status='COMPLETED',completed_at=?,result_json=? WHERE run_key=?",
                    (utcnow(), json.dumps(result), run_key),
                )
            return result
        owner = uuid.uuid4().hex
        try:
            self._acquire(owner)
            if self.workload == "execution":
                result = self.company.tasks.execute_pending()
            else:
                report = CEOReport(db).generate()
                result = {"report_date": report["data"]["date"]}
            next_run = (datetime.now(UTC) + timedelta(seconds=self.policy["interval"])).isoformat()
            with db.connect() as conn:
                conn.execute(
                    "UPDATE jobs SET status='IDLE',last_completed_at=?,next_run_at=?,last_error=NULL,"
                    "consecutive_failures=0 WHERE id=?",
                    (utcnow(), next_run, self.job_id),
                )
                conn.execute(
                    "UPDATE job_runs SET status='COMPLETED',completed_at=?,result_json=? WHERE run_key=?",
                    (utcnow(), json.dumps(result), run_key),
                )
            return result
        except Exception as exc:
            job = db.one("SELECT consecutive_failures FROM jobs WHERE id=?", (self.job_id,))
            failures = int(job["consecutive_failures"] or 0) + 1
            retry_at = (datetime.now(UTC) + timedelta(minutes=min(240, 10 * (2 ** min(failures - 1, 5))))).isoformat()
            with db.connect() as conn:
                conn.execute(
                    "UPDATE jobs SET status='RETRY',next_run_at=?,last_error=?,consecutive_failures=? WHERE id=?",
                    (retry_at, str(exc)[:1000], failures, self.job_id),
                )
                conn.execute("UPDATE job_runs SET status='FAILED',completed_at=? WHERE run_key=?", (utcnow(), run_key))
            raise
        finally:
            self._release(owner)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("workload", choices=sorted(JOB_POLICY))
    parser.add_argument("--run-key", default=os.getenv("VANTERA_RUN_KEY"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    result = RemoteWorkload(build_company(), args.workload).run_remote(args.run_key, force=args.force)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
