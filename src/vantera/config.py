from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_path: Path
    host: str = "127.0.0.1"
    port: int = 8000
    max_units: int = 5
    auto_create_score: float = 0.70
    discovery_limit: int = 8
    cycle_task_limit: int = 20
    cycle_interval_seconds: int = 21600

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_path=Path(os.getenv("VANTERA_DB", "vantera.db")).resolve(),
            host=os.getenv("VANTERA_HOST", "127.0.0.1"),
            port=int(os.getenv("VANTERA_PORT", "8000")),
            max_units=int(os.getenv("VANTERA_MAX_UNITS", "5")),
            auto_create_score=float(os.getenv("VANTERA_AUTO_CREATE_SCORE", "0.70")),
            discovery_limit=int(os.getenv("VANTERA_DISCOVERY_LIMIT", "8")),
            cycle_task_limit=int(os.getenv("VANTERA_TASK_LIMIT", "20")),
            cycle_interval_seconds=int(os.getenv("VANTERA_CYCLE_INTERVAL", "21600")),
        )
