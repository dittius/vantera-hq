from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from vantera.agents import NullOpportunityProvider
from vantera.config import Settings
from vantera.engine import Company
from vantera.llm_agents import ExecutiveRuntime, ModelResult, OpenAIResponsesProvider, run_multi_agent_review


class RecordingProvider:
    def __init__(self): self.calls = []
    def invoke(self, *, model, instructions, input_text):
        self.calls.append((model, instructions, input_text))
        return ModelResult(f"Executive output {len(self.calls)}", f"resp_{len(self.calls)}", model, 10, 5, 15)


class RealAgentRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.company = Company(Settings(Path(self.temp.name) / "agents.db"), NullOpportunityProvider())
        self.company.initialize()

    def tearDown(self): self.temp.cleanup()

    def test_persistent_distinct_executive_identities_and_cvs(self):
        profiles = self.company.db.query("SELECT agent_id,full_name,biography,cv_text FROM agent_profiles")
        self.assertEqual(8, len(profiles))
        self.assertEqual(8, len({p["full_name"] for p in profiles}))
        self.assertEqual(8, len({p["biography"] for p in profiles}))
        self.assertTrue(all("CAREER" in p["cv_text"] and "EDUCATION" in p["cv_text"] for p in profiles))

    def test_provider_invocation_memory_and_audit_persist(self):
        provider = RecordingProvider()
        result = ExecutiveRuntime(self.company.db, provider).run("cmo", "Distribution brief", "Find an organic route")
        self.assertEqual("COMPLETED", result["status"])
        self.assertEqual(1, len(provider.calls))
        self.assertTrue(self.company.db.one("SELECT id FROM agent_memories WHERE agent_id='cmo'"))
        self.assertEqual("COMPLETED", self.company.db.one("SELECT status FROM model_runs")["status"])

    def test_ceo_chat_is_model_backed_and_persistent(self):
        provider = RecordingProvider()
        result = ExecutiveRuntime(self.company.db, provider).ceo_chat("What is the priority?")
        self.assertEqual("ANSWERED", result["status"])
        self.assertEqual(2, self.company.db.one("SELECT COUNT(*) n FROM ceo_chat_messages")["n"])
        self.assertTrue(self.company.db.one("SELECT id FROM owner_directives"))

    def test_multi_agent_delegation_invokes_eight_distinct_runs(self):
        provider = RecordingProvider()
        result = run_multi_agent_review(self.company.db, "Review three hypotheses", provider)
        self.assertEqual("COMPLETED", result["status"])
        self.assertEqual(8, len(provider.calls))
        self.assertEqual(7, self.company.db.one("SELECT COUNT(*) n FROM agent_messages WHERE message_type='DELEGATION'")["n"])

    def test_missing_key_never_silently_pretends_to_be_ai(self):
        result = ExecutiveRuntime(self.company.db, OpenAIResponsesProvider(api_key=None)).run("ceo", "Decision", "Choose")
        self.assertEqual("BLOCKED", result["status"])
        run = self.company.db.one("SELECT status,provider_response_id,output_text FROM model_runs")
        self.assertEqual("BLOCKED", run["status"])
        self.assertIsNone(run["provider_response_id"])
        self.assertIsNone(run["output_text"])


if __name__ == "__main__": unittest.main()
