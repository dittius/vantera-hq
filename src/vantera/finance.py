from __future__ import annotations

import json
import uuid
from datetime import date
from typing import Any

from .db import Database, utcnow


class FinancialLedger:
    def __init__(self, db: Database):
        self.db = db

    def record_verified(self, *, business_unit_id: str | None, kind: str, amount_cents: int,
                        evidence_id: str, occurred_at: str | None = None) -> str:
        if kind not in {"REVENUE", "EXPENSE"}:
            raise ValueError("kind must be REVENUE or EXPENSE")
        evidence = self.db.one("SELECT verified FROM evidence WHERE id=?", (evidence_id,))
        if not evidence or not evidence["verified"]:
            raise ValueError("Financial records require verified external evidence")
        record_id = f"fin_{uuid.uuid4().hex[:12]}"
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO financial_records VALUES(?,?,?,?,?,?,?,?)",
                (record_id, business_unit_id, kind, amount_cents, "EUR", evidence_id,
                 occurred_at or utcnow(), utcnow()),
            )
        self.db.event("financial_recorded", "cfo", "VERIFIED RESULT",
                      {"kind": kind, "amount_cents": amount_cents, "evidence_id": evidence_id},
                      "business_unit", business_unit_id)
        return record_id

    def totals(self) -> dict[str, int]:
        today = date.today().isoformat()
        rows = self.db.query(
            "SELECT kind, COALESCE(SUM(amount_cents),0) total FROM financial_records WHERE substr(occurred_at,1,10)=? GROUP BY kind",
            (today,),
        )
        today_map = {row["kind"]: row["total"] for row in rows}
        all_rows = self.db.query("SELECT kind, COALESCE(SUM(amount_cents),0) total FROM financial_records GROUP BY kind")
        all_map = {row["kind"]: row["total"] for row in all_rows}
        month = today[:7]
        mtd = self.db.one(
            "SELECT COALESCE(SUM(amount_cents),0) total FROM financial_records WHERE kind='REVENUE' AND substr(occurred_at,1,7)=?",
            (month,),
        )
        revenue_today = today_map.get("REVENUE", 0)
        expenses_today = today_map.get("EXPENSE", 0)
        return {"revenue_today": revenue_today, "revenue_mtd": mtd["total"] if mtd else 0,
                "expenses_today": expenses_today, "profit_today": revenue_today - expenses_today,
                "total_verified_cash": all_map.get("REVENUE", 0) - all_map.get("EXPENSE", 0)}

    def create_evidence(self, kind: str, source: str, payload: dict[str, Any], *,
                        external_reference: str | None = None, verified: bool = False) -> str:
        evidence_id = f"ev_{uuid.uuid4().hex[:12]}"
        with self.db.connect() as conn:
            conn.execute("INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?)", (
                evidence_id, kind, source, external_reference, json.dumps(payload), int(verified),
                utcnow() if verified else None, utcnow()))
        return evidence_id

