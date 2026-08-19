from __future__ import annotations

import json
import os
import tempfile
import shutil
from datetime import UTC, datetime
from pathlib import Path


def _decoded(value):
    if value in (None, ""):
        return None
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value


def export_public_state(db, destination: Path | str) -> dict:
    """Atomically export the evidence-backed, non-secret state consumed by VANTERA HQ."""
    job = db.one(
        "SELECT status,last_started_at,last_completed_at,next_run_at,last_error,"
        "lease_expires_at,consecutive_failures FROM jobs WHERE id='job_company_cycle'"
    ) or {}
    remote_run = db.one(
        "SELECT run_key,started_at,completed_at,status FROM job_runs "
        "WHERE status='COMPLETED' ORDER BY completed_at DESC LIMIT 1"
    )
    tasks = db.query(
        "SELECT id,business_unit_id,title,assigned_agent,status,result_json,created_at,"
        "started_at,completed_at FROM tasks ORDER BY created_at DESC LIMIT 250"
    )
    events = db.query(
        "SELECT id,event_type,actor,entity_type,entity_id,phase,payload_json,occurred_at "
        "FROM events ORDER BY id DESC LIMIT 500"
    )
    units = db.query(
        "SELECT id,name,thesis,target_customer,monetization_model,responsible_executive,"
        "workers_json,kpi,status,launch_date,revenue_cents,expense_cents,created_at,updated_at "
        "FROM business_units ORDER BY created_at DESC"
    )
    reports = db.query(
        "SELECT id,report_date,body,data_json,created_at FROM reports "
        "ORDER BY report_date DESC LIMIT 90"
    )
    publications = db.query("SELECT business_unit_id,public_url,status,published_at,verified_at FROM venture_publications ORDER BY published_at DESC")
    distributions = db.query("SELECT business_unit_id,channel,action,public_reference,status,evidence_json,executed_at FROM distribution_actions ORDER BY executed_at DESC")
    for row in tasks:
        row["result"] = _decoded(row.pop("result_json"))
    for row in events:
        row["payload"] = _decoded(row.pop("payload_json"))
    for row in units:
        row["workers"] = _decoded(row.pop("workers_json")) or []
        row["profit_cents"] = row["revenue_cents"] - row["expense_cents"]
    for row in reports:
        row["data"] = _decoded(row.pop("data_json"))
    for row in distributions:
        row["evidence"] = _decoded(row.pop("evidence_json"))
    publication_map = {row["business_unit_id"]: row for row in publications}
    distribution_map = {}
    for row in distributions:
        distribution_map.setdefault(row["business_unit_id"], []).append(row)
    for row in units:
        row["publication"] = publication_map.get(row["id"])
        row["distribution_actions"] = distribution_map.get(row["id"], [])
    current = next((task for task in tasks if task["status"] == "EXECUTING"), None)
    state = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "autonomy": {
            "status": ("RECOVERY" if job.get("status") == "FAILED" else
                       "RUNNING" if remote_run else "AWAITING_REMOTE_AUTH"),
            "remote_verified": bool(remote_run),
            "remote_run_key": remote_run.get("run_key") if remote_run else None,
            "last_cycle": job.get("last_completed_at"),
            "current_cycle": job.get("last_started_at") if job.get("status") == "RUNNING" else None,
            "next_cycle": job.get("next_run_at"),
            "currently_executing": current,
            "last_success": job.get("last_completed_at"),
            "last_failure": job.get("last_error"),
            "consecutive_failures": job.get("consecutive_failures", 0),
        },
        "policy": {"autonomous_spend_limit_cents": 0, "currency": "EUR", "owner_operational": False},
        "agents": db.query("SELECT id,name,role,reports_to,status,created_at FROM agents ORDER BY created_at"),
        "business_units": units,
        "opportunities": db.query(
            "SELECT o.id,o.name,o.thesis,o.score,o.status,o.rationale,o.created_at,o.evaluated_at,"
            "COALESCE(json_array_length(r.evidence_json),0) evidence_count,r.research_json "
            "FROM opportunities o LEFT JOIN opportunity_research r ON r.opportunity_id=o.id "
            "ORDER BY o.created_at DESC LIMIT 250"
        ),
        "tasks": tasks,
        "events": events,
        "reports": reports,
        "venture_publications": publications,
        "distribution_actions": distributions,
    }
    for opportunity in state["opportunities"]:
        research = _decoded(opportunity.pop("research_json", None)) or {}
        opportunity["revenue_path"] = {
            "offer": research.get("offer") or research.get("monetization_method"),
            "payer": research.get("payer") or research.get("target_users"),
            "reason_to_pay": research.get("reason_to_pay"),
            "discovery": research.get("distribution_method"),
            "value_capture": research.get("value_capture") or research.get("path_to_first_revenue"),
            "autonomous_now": research.get("autonomous_now"),
            "external_authentication": research.get("external_authentication"),
        }
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=destination.parent, prefix="state-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False, separators=(",", ":"))
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
    # Keep the deployable PWA shell synchronized with every state export.
    public_root = destination.parent.parent
    static_root = Path(__file__).with_name("static")
    for asset in ("index.html", "app.css", "app.js", "manifest.webmanifest", "sw.js", "icon.svg"):
        shutil.copy2(static_root / asset, public_root / asset)
    return state
