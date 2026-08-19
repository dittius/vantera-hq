from __future__ import annotations

import json
import mimetypes
from datetime import UTC, datetime
from pathlib import Path
from wsgiref.simple_server import make_server

from .db import Database
from .finance import FinancialLedger
from .llm_agents import ExecutiveRuntime

STATIC = Path(__file__).with_name("static")


def _json(value, fallback=None):
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return fallback


class Dashboard:
    """Owner-facing, read-only VANTERA HQ and state API."""

    def __init__(self, db: Database): self.db = db

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "/")
        if path == "/api/state":
            return self._respond(start_response, json.dumps(self.state(), default=str).encode(), "application/json; charset=utf-8", cache="no-store")
        if path == "/api/ceo-chat" and environ.get("REQUEST_METHOD") == "POST":
            try:
                length = min(int(environ.get("CONTENT_LENGTH") or 0), 8000)
                payload = json.loads(environ["wsgi.input"].read(length) or b"{}")
                message = str(payload.get("message") or "").strip()
                if not message: raise ValueError("Message is required")
                result = ExecutiveRuntime(self.db).ceo_chat(message)
                status = "200 OK" if result.get("status") == "ANSWERED" else "503 Service Unavailable"
                return self._respond(start_response, json.dumps(result).encode(), "application/json; charset=utf-8", status, "no-store")
            except (ValueError, json.JSONDecodeError) as exc:
                return self._respond(start_response, json.dumps({"error": str(exc)}).encode(), "application/json", "400 Bad Request", "no-store")
        name = "index.html" if path == "/" else path.lstrip("/")
        file = (STATIC / name).resolve()
        if STATIC.resolve() not in file.parents or not file.is_file():
            return self._respond(start_response, b"Not found", "text/plain", "404 Not Found")
        content_type = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
        cache = "public, max-age=86400" if file.suffix in {".svg", ".css"} else "no-cache"
        return self._respond(start_response, file.read_bytes(), content_type, cache=cache)

    @staticmethod
    def _respond(start_response, payload, content_type, status="200 OK", cache="no-cache"):
        start_response(status, [("Content-Type", content_type), ("Content-Length", str(len(payload))), ("Cache-Control", cache), ("X-Content-Type-Options", "nosniff")])
        return [payload]

    def state(self):
        money = FinancialLedger(self.db).totals()
        units = self.db.query("SELECT * FROM business_units ORDER BY created_at DESC")
        tasks = self.db.query("SELECT * FROM tasks ORDER BY created_at DESC LIMIT 250")
        agents = self.db.query("SELECT * FROM agents ORDER BY role,name")
        opportunities = self.db.query("""SELECT o.*,r.decision,r.evidence_json,r.scorecard_json FROM opportunities o LEFT JOIN opportunity_research r ON r.opportunity_id=o.id ORDER BY o.created_at DESC LIMIT 100""")
        events = self.db.query("SELECT * FROM events ORDER BY id DESC LIMIT 100")
        reports = self.db.query("SELECT * FROM reports ORDER BY report_date DESC LIMIT 30")
        job = self.db.one("SELECT * FROM jobs WHERE name='company_cycle'") or {}
        current, recent = {}, {}
        for task in tasks:
            aid = task["assigned_agent"]
            if aid not in current and task["status"] in {"PLANNED", "PENDING", "EXECUTING", "RUNNING", "BLOCKED", "FAILED"}: current[aid] = task
            if aid not in recent and task["status"] in {"VERIFIED", "EXECUTED", "COMPLETED"}: recent[aid] = task
        unit_map = {u["id"]: u for u in units}
        workers = [self._worker(a, current.get(a["id"]), recent.get(a["id"]), unit_map) for a in agents]
        known = {a["id"] for a in agents}
        for unit in units:
            for item in (_json(unit.get("workers_json"), []) or []):
                wid = item.get("id") if isinstance(item, dict) else str(item)
                if not wid or wid in known: continue
                workers.append(self._worker({"id": wid, "name": item.get("name", wid) if isinstance(item, dict) else wid, "role": item.get("role", "Unit Worker") if isinstance(item, dict) else "Unit Worker", "reports_to": unit.get("responsible_executive")}, current.get(wid), recent.get(wid), unit_map, unit["id"]))
        activity = []
        for event in events:
            payload = _json(event.pop("payload_json", "{}"), {})
            activity.append(dict(event, payload=payload))
        report_rows = []
        for report in reports:
            data = _json(report.pop("data_json", "{}"), {})
            report_rows.append(dict(report, data=data))
        scheduled = bool(job.get("next_run_at"))
        profiles = self.db.query("SELECT agent_id,full_name,age,nationality,title,department,biography,education_json,career_json,skills_json,languages_json,traits_json,decision_style,objectives_json,cv_text,portrait_url,portrait_position,pixel_style_json FROM agent_profiles")
        for profile in profiles:
            for key in ("education_json","career_json","skills_json","languages_json","traits_json","objectives_json","pixel_style_json"):
                profile[key.removesuffix("_json")] = _json(profile.pop(key), [])
        audit = self.db.query("SELECT id,agent_id,model,purpose,tools_json,delegations_json,decision_summary,status,provider_response_id,input_tokens,output_tokens,total_tokens,started_at,completed_at FROM model_runs ORDER BY started_at DESC LIMIT 100")
        for run in audit:
            run["tools"] = _json(run.pop("tools_json"), [])
            run["delegations"] = _json(run.pop("delegations_json"), [])
        return {"generated_at": datetime.now(UTC).isoformat(),
            "autonomy": {"status": "RUNNING" if job.get("status") in {"RUNNING", "IDLE", "RETRY"} and scheduled else "NOT SCHEDULED", "current_cycle": job.get("last_started_at") if job.get("status") == "RUNNING" else None, "last_cycle": job.get("last_started_at"), "next_cycle": job.get("next_run_at"), "last_success": job.get("last_completed_at"), "last_failure": job.get("last_error"), "currently_executing": [t["title"] for t in tasks if t["status"] in {"EXECUTING", "RUNNING"}]},
            "money": {"verified_revenue_cents": money.get("revenue", money.get("total_verified_cash", 0)), "verified_expenses_cents": money.get("expenses", 0), "verified_profit_cents": money.get("profit", money.get("total_verified_cash", 0)), "verified_cash_cents": money.get("total_verified_cash", 0), "spend_limit_cents": 0},
            "units": units, "workers": workers, "opportunities": opportunities, "activity": activity, "reports": report_rows,
            "agent_profiles": profiles,
            "agent_communications": self.db.query("SELECT sender_id,recipient_id,message_type,content,business_unit_id,created_at FROM agent_messages ORDER BY created_at DESC LIMIT 100"),
            "agent_audit": audit,
            "ceo_chat": self.db.query("SELECT conversation_id,role,content,status,created_at FROM ceo_chat_messages ORDER BY created_at LIMIT 200"),
            "owner_action_required": "NONE"}

    @staticmethod
    def _worker(agent, task, recent, unit_map, forced_unit=None):
        unit_id = forced_unit or (task or {}).get("business_unit_id")
        status, action = (task or {}).get("status", "IDLE"), (task or {}).get("action_type", "")
        visual = "IDLE"
        if status in {"EXECUTING", "RUNNING"}: visual = {"research": "RESEARCHING", "build": "BUILDING", "code": "CODING", "write": "WRITING", "publish": "PUBLISHING", "verify": "VERIFYING"}.get(action.lower(), "THINKING")
        elif status in {"BLOCKED", "FAILED", "COMPLETED"}: visual = status
        return {"id": agent["id"], "name": agent["name"], "role": agent["role"], "executive_owner": agent.get("reports_to"), "business_unit_id": unit_id, "business_unit": (unit_map.get(unit_id) or {}).get("name"), "state": visual, "task": task, "latest_completed_task": recent, "activity_timestamp": (task or recent or {}).get("started_at") or (task or recent or {}).get("completed_at"), "kpi_contribution": (unit_map.get(unit_id) or {}).get("kpi")}


def serve(db: Database, host: str, port: int) -> None:
    print(f"VANTERA HQ: http://{host}:{port}")
    with make_server(host, port, Dashboard(db)) as server: server.serve_forever()
