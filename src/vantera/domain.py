from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class UnitStatus(StrEnum):
    IDEA = "IDEA"
    VALIDATING = "VALIDATING"
    BUILDING = "BUILDING"
    LAUNCHED = "LAUNCHED"
    GROWING = "GROWING"
    PIVOTING = "PIVOTING"
    TERMINATED = "TERMINATED"


class TaskStatus(StrEnum):
    PLANNED = "PLANNED"
    EXECUTING = "EXECUTING"
    EXECUTED = "EXECUTED"
    VERIFIED = "VERIFIED"
    BLOCKED = "BLOCKED"
    REJECTED = "REJECTED"


@dataclass
class Opportunity:
    name: str
    thesis: str
    target_customer: str
    monetization_model: str
    source: str
    capital_required_cents: int = 0
    human_operations_required: bool = False
    signals: dict[str, float] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    research: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
