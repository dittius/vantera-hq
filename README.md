# VANTERA

VANTERA is a local-first operating system for an autonomous, AI-native holding company. It is an application, not a role-play: state, tasks, decisions, evidence, finance, reports, and every operating-cycle event persist in SQLite.

The Owner is deliberately outside the operating system. The CEO can evaluate and create zero-capital units, execute registered tools, and generate the daily report without routine Owner approval.

## What works

- Persistent SQLite schema for company state, agents, opportunities, units, tasks, experiments, evidence, finance, decisions, events, and reports.
- Eight persistent executive-agent records coordinated under the CEO.
- Dynamic opportunity evaluation and business-unit creation (no hard-coded business types).
- Hard rejection of opportunities requiring capital or human operational work.
- Tool registry with replaceable action adapters.
- Task lifecycle: `PLANNED → EXECUTING → EXECUTED/VERIFIED/BLOCKED`.
- Append-only activity ledger with explicit `PLANNED`, `EXECUTED`, and `VERIFIED RESULT` phases.
- Financial ledger that refuses entries lacking verified evidence.
- Autonomous cycle: discover, evaluate, create, execute, verify, report.
- Daily CEO Owner Report and minimal read-only Owner dashboard.
- TikTok Shop Affiliate Unit seeded as a real portfolio record, with no fabricated launch or results.

## Run

Requires Python 3.11+ and no third-party package.

```powershell
$env:PYTHONPATH = "src"
python -m vantera init
python -m vantera cycle
python -m vantera serve
```

Open <http://127.0.0.1:8000>. The database defaults to `vantera.db`. Override with `VANTERA_DB`.

Run tests:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

Optional editable installation:

```powershell
python -m pip install -e .
vantera cycle
```

## Reality and integration boundaries

The normal opportunity provider uses current public-source evidence; the offline provider returns no opportunities rather than inventing discoveries. The default external-action adapter returns `BLOCKED`; it never claims publication, outreach, traffic, customers, or revenue.

Normal `cycle` operation now uses `PublicWebOpportunityProvider`, which queries the free HN Algolia public API for current problem/tool signals. It retains source URLs, publication and observation timestamps, public point/comment counts, structured research, and a permanent ten-factor scorecard. Use `cycle --offline` only for local operation without network discovery.

Recurring operation is available through `python -m vantera daemon`. It maintains a persistent job record, filesystem cycle lock, retry state, execution logs, a per-cycle venture cap, and a task execution limit. For unattended operation, launch this command at login through the operating system's user scheduler or process supervisor; no paid service is required.

The seeded TikTok unit currently prepares an internal validation playbook. Actual publishing, analytics, and affiliate transactions require authenticated TikTok Shop/publishing adapters and compliant account access. Organic discovery sources, email/CRM, web analytics, and transaction sources likewise need adapters. None is required for the core operating cycle to continue.

OpenAI is deliberately optional. A future LLM-backed executive implementation can use the OpenAI Agents SDK behind the existing provider and tool protocols without changing persistence or policy enforcement. Deterministic policy gates must remain outside model prompts.

## Important operational note

For indefinite local operation, schedule `python -m vantera cycle` with the host operating system. The application itself never spends money, enters contracts, impersonates human activity, or records unsupported results.
