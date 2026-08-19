from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from vantera.agents import NullOpportunityProvider
from vantera.config import Settings
from vantera.engine import Company
from vantera.llm_agents import (ExecutiveRuntime, GeminiProvider, ModelQuotaExhausted,
    ModelResult, OpenAIResponsesProvider, select_provider, run_multi_agent_review)


class RecordingProvider:
    def __init__(self): self.calls = []
    def invoke(self, *, model, instructions, input_text):
        self.calls.append((model, instructions, input_text))
        return ModelResult(f"Executive output {len(self.calls)}", f"resp_{len(self.calls)}", model, 10, 5, 15)

class QuotaProvider(RecordingProvider):
    name = "gemini"
    default_model = "gemini-2.5-flash-lite"
    def invoke(self, **kwargs): raise ModelQuotaExhausted("quota")

class FakeGemini(GeminiProvider):
    def _request(self, url, body=None):
        if body is None:
            return {"models":[{"name":"models/gemini-2.5-flash-lite","supportedGenerationMethods":["generateContent"]}]}
        return {"responseId":"g1","modelVersion":"gemini-2.5-flash-lite","candidates":[{"content":{"parts":[{"text":"Genuine remote output"}]}}],"usageMetadata":{"promptTokenCount":4,"candidatesTokenCount":3,"totalTokenCount":7}}


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

    def test_gemini_discovers_stable_model_and_persists_provider_usage(self):
        result = ExecutiveRuntime(self.company.db, FakeGemini("secret")).run("ceo", "Decision", "Choose")
        self.assertEqual("COMPLETED", result["status"])
        run = self.company.db.one("SELECT provider,model,request_status,total_tokens FROM model_runs")
        self.assertEqual(("gemini","gemini-2.5-flash-lite","SUCCESS",7), tuple(run.values()))
        self.assertEqual(1, self.company.db.one("SELECT request_count FROM model_daily_usage")["request_count"])

    def test_quota_never_fakes_output_and_persists_retry_work(self):
        result = ExecutiveRuntime(self.company.db, QuotaProvider()).run("cvo", "Discovery", "Find hypotheses")
        self.assertEqual("WAITING_FOR_MODEL_QUOTA", result["status"])
        self.assertIsNone(self.company.db.one("SELECT output_text FROM model_runs")["output_text"])
        self.assertEqual("WAITING_FOR_MODEL_QUOTA", self.company.db.one("SELECT status FROM model_work_queue")["status"])


if __name__ == "__main__": unittest.main()
