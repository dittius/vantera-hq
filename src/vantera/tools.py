from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from pathlib import Path
import html


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
    def __init__(self, asset_root: Path | str = "ventures") -> None:
        self._tools: dict[str, ActionTool] = {}
        self.register(PrepareAssetTool())
        self.register(ExternalAdapterTool())
        self.register(BuildLandingPageTool(asset_root))

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
