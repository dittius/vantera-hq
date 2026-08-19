from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .agents import NullOpportunityProvider, PublicWebOpportunityProvider
from .config import Settings
from .engine import CEOReport, Company
from .web import serve
from .scheduler import Scheduler
from .production import export_public_state


def build_company(offline: bool = False) -> Company:
    settings = Settings.from_env()
    provider = NullOpportunityProvider() if offline else PublicWebOpportunityProvider(settings.discovery_limit)
    company = Company(settings, provider)
    company.initialize()
    return company


def main() -> None:
    parser = argparse.ArgumentParser(prog="vantera")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Initialize storage and seed the first unit")
    cycle = sub.add_parser("cycle", help="Run one evidence-backed autonomous operating cycle")
    cycle.add_argument("--offline", action="store_true", help="Disable public discovery")
    daemon = sub.add_parser("daemon", help="Run guarded recurring cycles")
    daemon.add_argument("--max-cycles", type=int)
    sub.add_parser("report", help="Print the current CEO daily report")
    sub.add_parser("status", help="Print compact machine-readable state")
    remote = sub.add_parser("remote-cycle", help="Run a due cycle using a durable delivery key")
    remote.add_argument("--run-key", default=os.getenv("VANTERA_RUN_KEY"))
    remote.add_argument("--force", action="store_true", help="Force this authenticated delivery to run now")
    export = sub.add_parser("export", help="Export sanitized state for VANTERA HQ")
    export.add_argument("--output", default="public/data/state.json")
    web = sub.add_parser("serve", help="Start the Owner control panel")
    web.add_argument("--host")
    web.add_argument("--port", type=int)
    args = parser.parse_args()
    company = build_company(getattr(args, "offline", False))
    if args.command == "init":
        print(f"Initialized {company.settings.database_path}")
    elif args.command == "cycle":
        print(json.dumps(Scheduler(company).run_once(), indent=2))
    elif args.command == "daemon":
        Scheduler(company).daemon(args.max_cycles)
    elif args.command == "report":
        print(CEOReport(company.db).generate()["body"])
    elif args.command == "status":
        units = company.db.query("SELECT id,name,status,revenue_cents,expense_cents FROM business_units")
        tasks = company.db.query("SELECT id,title,status FROM tasks")
        print(json.dumps({"units": units, "tasks": tasks}, indent=2))
    elif args.command == "remote-cycle":
        print(json.dumps(Scheduler(company).run_remote(args.run_key, force=args.force), indent=2))
    elif args.command == "export":
        state = export_public_state(company.db, Path(args.output))
        print(json.dumps({"output": str(Path(args.output).resolve()), "generated_at": state["generated_at"]}))
    elif args.command == "serve":
        serve(company.db, args.host or company.settings.host, args.port or company.settings.port)


if __name__ == "__main__":
    main()
