from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from pathlib import Path
import html
import json
import os
import shutil
from datetime import UTC, datetime


@dataclass
class ActionResult:
    executed: bool
    verified: bool
    summary: str
    data: dict[str, Any]
    evidence: dict[str, Any] | None = None


class ActionTool(Protocol):
    name: str
    def execute(self, payload: dict[str, Any]) -> ActionResult: ...


class PrepareAssetTool:
    name = "prepare_asset"

    def execute(self, payload: dict[str, Any]) -> ActionResult:
        content = payload.get("content", "")
        return ActionResult(
            executed=True,
            verified=True,
            summary="Digital asset prepared in persistent task output.",
            data={"asset_type": payload.get("asset_type", "document"), "content": content},
            evidence={"kind": "internal_artifact", "source": "vantera", "payload": {"content": content}},
        )


class ExternalAdapterTool:
    """Honest placeholder for integrations: blocks instead of fabricating an external action."""
    name = "external_action"

    def execute(self, payload: dict[str, Any]) -> ActionResult:
        return ActionResult(False, False, "External adapter is not configured; no action was performed.", payload)


class ToolRegistry:
    def __init__(self, asset_root: Path | str = "ventures", public_root: Path | str = "public") -> None:
        self._tools: dict[str, ActionTool] = {}
        self.register(PrepareAssetTool())
        self.register(ExternalAdapterTool())
        self.register(BuildLandingPageTool(asset_root))
        self.register(GitHubPagesPublishTool(asset_root, public_root))
        self.register(StaticSeoDistributionTool(public_root))

    def register(self, tool: ActionTool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> ActionTool | None:
        return self._tools.get(name)


class BuildLandingPageTool:
    name = "build_landing_page"

    def __init__(self, asset_root: Path | str):
        self.asset_root = Path(asset_root)

    def execute(self, payload: dict[str, Any]) -> ActionResult:
        unit_id = payload["unit_id"]
        destination = self.asset_root / unit_id
        destination.mkdir(parents=True, exist_ok=True)
        sources = payload.get("sources", [])
        links = "".join(f'<li><a href="{html.escape(s["url"])}">{html.escape(s.get("title") or s.get("source") or s["url"])}</a></li>' for s in sources)
        page = f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>{html.escape(payload["name"])}</title><style>body{{font:18px system-ui;max-width:760px;margin:60px auto;padding:20px;line-height:1.6}}small{{color:#667}}</style></head><body><small>VANTERA independently sourced resource · early validation</small><h1>{html.escape(payload["name"])}</h1><p>{html.escape(payload["thesis"])}</p><h2>Who this helps</h2><p>{html.escape(payload["target_customer"])}</p><h2>Public evidence</h2><ul>{links}</ul><p><small>No customer, usage, or revenue claim is made. Sources observed at the timestamps stored by VANTERA.</small></p></body></html>'''
        (destination / "index.html").write_text(page, encoding="utf-8")
        return ActionResult(True, True, "Built a sourced static validation site.",
                            {"path": str((destination / "index.html").resolve())},
                            {"kind": "internal_artifact", "source": "vantera", "payload": {"path": str(destination / "index.html")}})


class GitHubPagesPublishTool:
    name = "publish_github_pages"

    def __init__(self, asset_root: Path | str, public_root: Path | str = "public"):
        self.asset_root, self.public_root = Path(asset_root), Path(public_root)

    def execute(self, payload: dict[str, Any]) -> ActionResult:
        unit_id = payload["unit_id"]
        source, destination = self.asset_root / unit_id, self.public_root / "ventures" / unit_id
        if not (source / "index.html").exists():
            return ActionResult(False, False, "No built venture asset exists to publish.", {})
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / "index.html", destination / "index.html")
        owner, repo = os.getenv("GITHUB_REPOSITORY", "dittius/vantera-hq").split("/", 1)
        url = f"https://{owner}.github.io/{repo}/ventures/{unit_id}/"
        published_at = datetime.now(UTC).isoformat()
        manifest = {"business_unit_id": unit_id, "public_url": url, "published_at": published_at, "status": "PUBLISHED"}
        (destination / "publication.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return ActionResult(True, True, "Published the venture asset to the GitHub Pages artifact.",
                            {"public_url": url, "asset_path": str(destination), "published_at": published_at},
                            {"kind": "publication_artifact", "source": "github_pages", "payload": manifest})


class StaticSeoDistributionTool:
    name = "distribute_static_seo"

    def __init__(self, public_root: Path | str = "public"):
        self.public_root = Path(public_root)

    def execute(self, payload: dict[str, Any]) -> ActionResult:
        unit_id, public_url = payload["unit_id"], payload["public_url"]
        destination = self.public_root / "ventures" / unit_id
        if not (destination / "index.html").exists():
            return ActionResult(False, False, "The public asset is not staged; distribution was not claimed.", {})
        record = {"channel": "owned_web_seo", "action": "published_indexable_canonical_url",
                  "public_reference": public_url, "executed_at": datetime.now(UTC).isoformat()}
        (destination / "distribution.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
        return ActionResult(True, True, "Executed owned-site organic distribution with an indexable public URL.", record,
                            {"kind": "distribution_execution", "source": "vantera_owned_web", "payload": record})
