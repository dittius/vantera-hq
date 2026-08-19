from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
import json
import re
import urllib.parse
import urllib.request
from datetime import UTC, datetime

from .domain import Opportunity


EXECUTIVES = (
    ("ceo", "VANTERA CEO", "Chief Executive Officer", None),
    ("cvo", "Chief Venture Officer", "Venture discovery", "ceo"),
    ("cso", "Chief Strategy Officer", "Strategy and evaluation", "ceo"),
    ("cto", "Chief Technology Officer", "Software and automation", "ceo"),
    ("cmo", "Chief Marketing Officer", "Organic acquisition", "ceo"),
    ("sales", "Chief Sales Officer", "Autonomous sales systems", "ceo"),
    ("coo", "Chief Operating Officer", "Portfolio operations", "ceo"),
    ("cfo", "Chief Financial Officer", "Verified finance", "ceo"),
)


class OpportunityProvider(Protocol):
    def discover(self) -> list[Opportunity]: ...


class NullOpportunityProvider:
    """Safe default: no invented market discoveries."""

    def discover(self) -> list[Opportunity]:
        return []


class PublicWebOpportunityProvider:
    """Discovers current, source-linked problems and launches through free HN public search."""

    endpoint = "https://hn.algolia.com/api/v1/search_by_date"

    def __init__(self, limit: int = 8, timeout: int = 15):
        self.limit, self.timeout = limit, timeout

    def _search(self, query: str, tags: str = "story") -> list[dict]:
        params = urllib.parse.urlencode({"query": query, "tags": tags, "hitsPerPage": self.limit})
        request = urllib.request.Request(f"{self.endpoint}?{params}", headers={"User-Agent": "VANTERA/0.2 public-research"})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.load(response).get("hits", [])

    def discover(self) -> list[Opportunity]:
        hits: dict[str, dict] = {}
        for query in ("Ask HN how do I", "Ask HN tool for", "Show HN"):
            for hit in self._search(query):
                if hit.get("objectID") and hit.get("title"):
                    hits[hit["objectID"]] = hit
        ranked = sorted(hits.values(), key=lambda h: (h.get("points") or 0) + 2 * (h.get("num_comments") or 0), reverse=True)
        opportunities = []
        for hit in ranked[: self.limit]:
            title = re.sub(r"^(Ask|Show) HN:\s*", "", hit["title"], flags=re.I).strip()
            story_url = f"https://news.ycombinator.com/item?id={hit['objectID']}"
            target_url = hit.get("url")
            points, comments = hit.get("points") or 0, hit.get("num_comments") or 0
            demand = min(1.0, (points + comments * 2) / 120)
            opportunity_name = f"Resource for: {title[:72]}"
            evidence = [{"url": story_url, "observed_at": datetime.now(UTC).isoformat(),
                         "published_at": hit.get("created_at"), "source": "Hacker News",
                         "title": hit["title"], "points": points, "comments": comments}]
            if target_url:
                evidence.append({"url": target_url, "observed_at": datetime.now(UTC).isoformat(), "source": "linked project"})
            keywords = " ".join(word for word in re.findall(r"[A-Za-z0-9]+", title) if len(word) > 3)[:80]
            related = []
            if keywords:
                try:
                    for candidate in self._search(keywords)[:3]:
                        if candidate.get("objectID") != hit["objectID"] and candidate.get("title"):
                            related_url = candidate.get("url") or f"https://news.ycombinator.com/item?id={candidate['objectID']}"
                            related.append({"name": candidate["title"], "url": related_url,
                                            "points": candidate.get("points") or 0,
                                            "observed_at": datetime.now(UTC).isoformat()})
                            evidence.append({"url": related_url, "observed_at": datetime.now(UTC).isoformat(),
                                             "source": "related public result", "title": candidate["title"]})
                except Exception:
                    related = []
            research = {
                "problem": title, "target_users": "People discussing or adopting this problem/tool category",
                "existing_competitors": related or ([{"name": "Linked project", "url": target_url}] if target_url else []),
                "demand_evidence": f"Public discussion recorded {points} points and {comments} comments at discovery time.",
                "monetization_method": "Free curated resource; optional affiliate referrals or sponsorship only after audience exists",
                "distribution_method": "Indexable zero-cost website and useful community-safe content",
                "technical_feasibility": "Static site and structured public-source dataset; no paid infrastructure required",
                "required_accounts": [], "required_human_involvement": "None for build; publishing adapters required for third-party posting",
                "initial_capital_required": 0, "mandatory_paid_costs": 0,
                "legal_platform_constraints": "Respect source terms, copyright, robots rules, and community anti-spam policies",
                "path_to_first_revenue": "Publish useful comparison data, earn organic discovery, then add eligible referral links or inbound sponsorship",
            }
            opportunities.append(Opportunity(
                opportunity_name, f"A current public signal suggests demand around {title}. Validate with a useful sourced resource.",
                research["target_users"], research["monetization_method"], "HN Algolia public API",
                signals={"demand": demand, "automation": .95, "distribution": .7, "margin": .95,
                         "speed_launch": .95, "speed_revenue": .45, "competition": .45,
                         "technical_difficulty": .15, "dependency_risk": .2, "scalability": .8},
                evidence=evidence, research=research))
        return opportunities


@dataclass
class ExecutiveAgent:
    id: str
    name: str
    role: str
    reports_to: str | None


def executive_agents() -> list[ExecutiveAgent]:
    return [ExecutiveAgent(*row) for row in EXECUTIVES]
