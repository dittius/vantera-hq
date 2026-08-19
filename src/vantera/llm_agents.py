from __future__ import annotations

import json
import os
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from .db import utcnow


def ident(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


@dataclass
class ModelResult:
    text: str
    response_id: str | None
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


class ModelProvider(Protocol):
    def invoke(self, *, model: str, instructions: str, input_text: str) -> ModelResult: ...


class MissingModelCredential(RuntimeError):
    pass


class OpenAIResponsesProvider:
    """Real Responses API provider. There is intentionally no deterministic fallback."""

    endpoint = "https://api.openai.com/v1/responses"

    def __init__(self, api_key: str | None = None, timeout: int = 90):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.timeout = timeout

    def invoke(self, *, model: str, instructions: str, input_text: str) -> ModelResult:
        if not self.api_key:
            raise MissingModelCredential("OPENAI_API_KEY is not configured; no model invocation occurred")
        body = json.dumps({"model": model, "instructions": instructions, "input": input_text,
                           "store": False, "max_output_tokens": 1200}).encode()
        request = urllib.request.Request(self.endpoint, data=body, method="POST", headers={
            "Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
            "User-Agent": "VANTERA/1.0 real-agent-runtime"})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.load(response)
        text = payload.get("output_text") or "".join(
            content.get("text", "") for item in payload.get("output", [])
            for content in item.get("content", []) if content.get("type") == "output_text")
        usage = payload.get("usage") or {}
        return ModelResult(text.strip(), payload.get("id"), payload.get("model", model),
                           usage.get("input_tokens"), usage.get("output_tokens"), usage.get("total_tokens"))


class ExecutiveRuntime:
    def __init__(self, db, provider: ModelProvider | None = None, model: str | None = None):
        self.db = db
        self.provider = provider or OpenAIResponsesProvider()
        self.model = model or os.getenv("VANTERA_AGENT_MODEL", "gpt-5-mini")

    def context(self, agent_id: str) -> str:
        profile = self.db.one("SELECT * FROM agent_profiles WHERE agent_id=?", (agent_id,)) or {}
        memories = self.db.query("SELECT memory_type,subject,content FROM agent_memories WHERE agent_id=? ORDER BY importance DESC,created_at DESC LIMIT 12", (agent_id,))
        directives = self.db.query("SELECT content,status FROM owner_directives WHERE status!='REVOKED' ORDER BY created_at DESC LIMIT 10")
        units = self.db.query("SELECT name,status,thesis,target_customer,monetization_model,revenue_cents,expense_cents FROM business_units ORDER BY updated_at DESC")
        facts = {"identity": {k: profile.get(k) for k in ("full_name","title","department","biography","decision_style")},
                 "memories": memories, "owner_directives": directives, "business_units": units,
                 "policy": {"autonomous_spend_limit_eur": 0, "revenue_requires_external_evidence": True,
                            "owner_is_non_operational": True}}
        return json.dumps(facts, ensure_ascii=False)

    def run(self, agent_id: str, purpose: str, task: str, *, evidence_refs: list[str] | None = None,
            tools: list[str] | None = None, delegations: list[str] | None = None) -> dict[str, Any]:
        run_id, started = ident("run"), utcnow()
        evidence_refs, tools, delegations = evidence_refs or [], tools or [], delegations or []
        profile = self.db.one("SELECT full_name,title,responsibilities_json,authority_limits_json FROM agent_profiles WHERE agent_id=?", (agent_id,))
        if not profile:
            raise ValueError(f"Unknown persistent executive {agent_id}")
        instructions = (f"You are {profile['full_name']}, {profile['title']} of VANTERA, a real autonomous company. "
                        "Reason as this executive using supplied verified context. Never claim external execution or money without evidence. "
                        "Give a concise executive output and concise observable rationale; never reveal private chain-of-thought. "
                        f"Responsibilities: {profile['responsibilities_json']}. Limits: {profile['authority_limits_json']}.")
        input_text = f"PURPOSE\n{purpose}\n\nTASK\n{task}\n\nLIVE CONTEXT\n{self.context(agent_id)}"
        with self.db.connect() as conn:
            conn.execute("INSERT INTO model_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (run_id, agent_id, self.model, purpose, input_text[:4000], json.dumps(evidence_refs),
                          json.dumps(tools), json.dumps(delegations), None, None, "RUNNING", None,
                          None, None, None, None, started, None))
        try:
            result = self.provider.invoke(model=self.model, instructions=instructions, input_text=input_text)
            summary = result.text[:600]
            with self.db.connect() as conn:
                conn.execute("UPDATE model_runs SET model=?,output_text=?,decision_summary=?,status='COMPLETED',provider_response_id=?,input_tokens=?,output_tokens=?,total_tokens=?,completed_at=? WHERE id=?",
                             (result.model, result.text, summary, result.response_id, result.input_tokens,
                              result.output_tokens, result.total_tokens, utcnow(), run_id))
                conn.execute("INSERT INTO agent_memories VALUES(?,?,?,?,?,?,?,?,?)",
                             (ident("mem"), agent_id, "decision", purpose, summary, .7, run_id, utcnow(), None))
            self.db.event("real_agent_completed", agent_id, "VERIFIED RESULT", {"model": result.model, "run_id": run_id, "summary": summary})
            return {"run_id": run_id, "status": "COMPLETED", "output": result.text, "model": result.model}
        except Exception as exc:
            with self.db.connect() as conn:
                conn.execute("UPDATE model_runs SET status='BLOCKED',error=?,completed_at=? WHERE id=?", (str(exc), utcnow(), run_id))
            self.db.event("real_agent_blocked", agent_id, "BLOCKED", {"run_id": run_id, "reason": str(exc)})
            return {"run_id": run_id, "status": "BLOCKED", "error": str(exc), "model": self.model}

    def delegate(self, sender: str, recipient: str, content: str, *, business_unit_id: str | None = None) -> str:
        message_id = ident("msg")
        with self.db.connect() as conn:
            conn.execute("INSERT INTO agent_messages VALUES(?,?,?,?,?,?,?,?,?)",
                         (message_id, ident("conv"), sender, recipient, "DELEGATION", content, None, business_unit_id, utcnow()))
        self.db.event("agent_delegated", sender, "EXECUTED", {"recipient": recipient, "summary": content[:300]})
        return message_id

    def ceo_chat(self, message: str, conversation_id: str = "owner-ceo") -> dict[str, Any]:
        owner_id = ident("chat")
        with self.db.connect() as conn:
            conn.execute("INSERT INTO ceo_chat_messages VALUES(?,?,?,?,?,?,?)",
                         (owner_id, conversation_id, "owner", message, None, "RECEIVED", utcnow()))
            conn.execute("INSERT INTO owner_directives VALUES(?,?,?,?,?,?)",
                         (ident("directive"), message, "PENDING_INTERPRETATION", None, utcnow(), None))
        result = self.run("ceo", "Owner conversation and strategic instruction", message,
                          tools=["company_state", "portfolio", "verified_finance"], delegations=[])
        if result["status"] != "COMPLETED":
            return {"status": "BLOCKED", "message_id": owner_id, "error": result["error"]}
        response_id = ident("chat")
        with self.db.connect() as conn:
            conn.execute("INSERT INTO ceo_chat_messages VALUES(?,?,?,?,?,?,?)",
                         (response_id, conversation_id, "ceo", result["output"], result["run_id"], "ANSWERED", utcnow()))
        return {"status": "ANSWERED", "message_id": response_id, "response": result["output"], "model_run_id": result["run_id"]}


EXECUTIVE_REVIEW_ORDER = ("cvo", "cso", "cto", "cmo", "sales", "cfo", "coo")


def run_multi_agent_review(db, prompt: str, provider: ModelProvider | None = None) -> dict[str, Any]:
    runtime = ExecutiveRuntime(db, provider)
    briefs = []
    previous = ""
    for agent_id in EXECUTIVE_REVIEW_ORDER:
        runtime.delegate("ceo" if not briefs else EXECUTIVE_REVIEW_ORDER[len(briefs)-1], agent_id,
                         f"Prepare your executive brief for: {prompt}")
        result = runtime.run(agent_id, "Executive commercial review", prompt + "\nPrevious brief:\n" + previous[:1200],
                             delegations=[agent_id])
        briefs.append({"agent_id": agent_id, **result})
        if result["status"] != "COMPLETED":
            return {"status": "BLOCKED", "briefs": briefs, "reason": result.get("error")}
        previous = result["output"]
    decision = runtime.run("ceo", "Final portfolio decision", prompt + "\nExecutive briefs:\n" + json.dumps(briefs),
                           delegations=list(EXECUTIVE_REVIEW_ORDER))
    return {"status": decision["status"], "briefs": briefs, "decision": decision}
