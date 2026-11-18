"""The assistant on OpenAI: full turns through the real adapter, and the
embedding space each provider's vectors live in.

No network: the adapter's HTTP session is a fake that replays scripted
OpenAI payloads (see listings.test_openai_client).
"""

from __future__ import annotations

import json
from typing import Any
from unittest import mock

import requests
from django.core.cache import cache
from django.test import TestCase

from listings.openai_client import OpenAIClient
from listings.test_openai_client import OPENAI_ENV, FakeHttpResponse, FakeSession, text_payload

from . import engine, rag, semantic_cache
from .models import ConciergeThread, KnowledgeChunk
from .seeding import ensure_seeded, reseed_knowledge
from .tests import PLAN, REFLECT_PASS, ConciergeBaseTestCase, _fake_remote_vectors, make_listing, make_user

GEMINI_ENV = {"OPENAI_API_KEY": "", "GEMINI_API_KEY": "k", "GOOGLE_API_KEY": "", "AI_PROVIDER": ""}
NO_KEY_ENV = {"OPENAI_API_KEY": "", "GEMINI_API_KEY": "", "GOOGLE_API_KEY": "", "AI_PROVIDER": ""}


def tool_call_payload(*calls: tuple[str, str, dict[str, Any]]) -> dict[str, Any]:
    """tool_call_payload(("call_1", "search_listings", {"query": "lamp"}), ...)"""
    output: list[dict[str, Any]] = [{"type": "reasoning", "id": "rs_1", "encrypted_content": "opaque"}]
    for call_id, name, arguments in calls:
        output.append(
            {"type": "function_call", "id": f"fc_{call_id}", "call_id": call_id, "name": name, "arguments": json.dumps(arguments)}
        )
    return {
        "status": "completed",
        "output": output,
        "usage": {"input_tokens": 200, "output_tokens": 60, "output_tokens_details": {"reasoning_tokens": 40}, "total_tokens": 260},
    }


class EngineOverOpenAITests(ConciergeBaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = make_user()
        self.thread = ConciergeThread.objects.create(user=self.user)

    def run_turn(self, session: FakeSession, text: str) -> dict[str, Any]:
        client = OpenAIClient(api_key="sk-test-key", session=session)
        with mock.patch.dict("os.environ", OPENAI_ENV):
            return engine.run_turn(self.user, self.thread, text, client=client)

    def test_chat_turn(self) -> None:
        session = FakeSession(text_payload("Welcome to ReNest!"))
        result = self.run_turn(session, "hi")
        self.assertEqual(result["reply"], "Welcome to ReNest!")
        self.assertFalse(result["degraded"])
        (post,) = session.posts
        self.assertEqual(post["json"]["model"], engine.settings.CONCIERGE_MODEL_FAST)
        self.assertEqual(post["json"]["input"][-1], {"role": "user", "content": "hi"})
        # The engine's per-request timeout reaches the HTTP call.
        self.assertLessEqual(post["timeout"][1], engine.REQUEST_TIMEOUT_SECONDS)

    def test_task_turn_runs_a_tool_and_sends_its_result_back(self) -> None:
        make_listing(make_user("seller@test.edu"), title="Clip-on lamp")
        session = FakeSession(
            text_payload(json.dumps(PLAN)),                             # 1 plan
            tool_call_payload(("call_1", "get_my_activity", {})),       # 2 execute
            text_payload("You have no pending requests."),              # 3 draft
            text_payload(json.dumps(REFLECT_PASS)),                     # 4 reflect
        )
        result = self.run_turn(session, "do I have requests on my listings?")
        self.assertEqual(result["reply"], "You have no pending requests.")
        self.assertEqual(result["used_tools"], ["get_my_activity"])
        self.assertFalse(result["degraded"])
        self.assertTrue(result["meta"]["reflection"]["passed"])

        bodies = [post["json"] for post in session.posts]
        self.assertEqual(len(bodies), 4)
        self.assertEqual(bodies[1]["model"], engine.settings.CONCIERGE_MODEL_REASONING)
        self.assertIn("get_my_activity", [tool["name"] for tool in bodies[1]["tools"]])
        self.assertTrue(all(tool["type"] == "function" and tool["strict"] is False for tool in bodies[1]["tools"]))
        # The follow-up request replays the model's turn (reasoning first),
        # then answers the call under the id OpenAI gave it.
        reasoning, call, output = bodies[2]["input"][-3:]
        self.assertEqual((reasoning["type"], call["type"], output["type"]), ("reasoning", "function_call", "function_call_output"))
        self.assertEqual(output["call_id"], "call_1")
        self.assertIsInstance(json.loads(output["output"]), dict)

        usage = result["meta"]["usage"]
        self.assertEqual(usage["model_calls"], 4)
        self.assertEqual(usage["total_tokens"], 14 + 260 + 14 + 14)
        self.assertEqual(usage["thoughts_tokens"], 40)

    def test_listing_cards_reach_the_reply(self) -> None:
        listing = make_listing(make_user("seller@test.edu"), title="Clip-on lamp")
        session = FakeSession(
            text_payload(json.dumps(PLAN)),
            tool_call_payload(("call_1", "search_listings", {"query": "lamp"})),
            tool_call_payload(
                ("call_2", "show_listing_cards", {"listing_ids": [listing.id]}),
                ("call_3", "suggest_followups", {"suggestions": ["Reserve it?"]}),
            ),
            text_payload("Here's the lamp I found."),
            text_payload(json.dumps(REFLECT_PASS)),
        )
        result = self.run_turn(session, "what lamps are available right now")
        blocks = result["meta"]["ui_blocks"]
        self.assertEqual([block["type"] for block in blocks], ["listing_cards", "suggestions"])
        self.assertEqual(blocks[0]["props"]["listings"][0]["title"], "Clip-on lamp")
        # Two calls in one model turn get two results, each under its own id.
        outputs = [item for item in session.posts[3]["json"]["input"] if item.get("type") == "function_call_output"]
        self.assertEqual([item["call_id"] for item in outputs], ["call_1", "call_2", "call_3"])

    def test_a_rate_limit_moves_the_turn_to_the_fast_model(self) -> None:
        limited = FakeHttpResponse({"error": {"message": "Rate limit reached.", "type": "rate_limit_error"}}, status=429)
        session = FakeSession(
            limited,                                                    # plan on the reasoning model
            text_payload(json.dumps(PLAN)),                             # plan again on the fast model
            text_payload("You have nothing reserved."),
            text_payload(json.dumps(REFLECT_PASS)),
        )
        with mock.patch.object(engine.time, "sleep"), self.assertLogs("concierge.engine", level="WARNING"):
            result = self.run_turn(session, "what reservations do I have")
        self.assertEqual(result["reply"], "You have nothing reserved.")
        models = [post["json"]["model"] for post in session.posts]
        self.assertEqual(models[0], engine.settings.CONCIERGE_MODEL_REASONING)
        self.assertEqual(set(models[1:]), {engine.settings.CONCIERGE_MODEL_FAST})

    def test_timeouts_degrade_the_turn_instead_of_failing_it(self) -> None:
        session = FakeSession(*[requests.Timeout("read timed out")] * engine.MAX_ATTEMPTS)
        with mock.patch.object(engine.time, "sleep"), self.assertLogs("concierge.engine", level="ERROR"):
            result = self.run_turn(session, "hi")
        self.assertTrue(result["degraded"])
        self.assertEqual(result["meta"]["degraded_reason"], "timeout")

    def test_no_key_means_the_assistant_is_off(self) -> None:
        with mock.patch.dict("os.environ", NO_KEY_ENV):
            self.assertFalse(engine.is_enabled())
        with mock.patch.dict("os.environ", OPENAI_ENV):
            self.assertTrue(engine.is_enabled())


class EmbeddingSpacePerProviderTests(TestCase):
    """Vectors from OpenAI and from Gemini are different spaces. A corpus
    embedded by one is never searched with a query embedded by the other."""

    def setUp(self) -> None:
        cache.clear()
        rag._remote_down_until = 0.0

    def test_each_provider_has_its_own_space(self) -> None:
        with mock.patch.dict("os.environ", OPENAI_ENV):
            self.assertEqual(rag.remote_space(), rag.SPACE_OPENAI)
        with mock.patch.dict("os.environ", GEMINI_ENV):
            self.assertEqual(rag.remote_space(), rag.SPACE_REMOTE)
        self.assertNotEqual(rag.SPACE_OPENAI, rag.SPACE_REMOTE)

    def test_documents_are_tagged_with_the_active_providers_space(self) -> None:
        with mock.patch.dict("os.environ", OPENAI_ENV), \
                mock.patch.object(rag, "_embed_remote", side_effect=_fake_remote_vectors):
            _, space = rag.embed_documents(["handoff pin"])
        self.assertEqual(space, rag.SPACE_OPENAI)

    def test_embedding_calls_go_through_the_adapter(self) -> None:
        vector = [3.0, 4.0] + [0.0] * (rag.REMOTE_DIM - 2)
        session = FakeSession({"data": [{"index": 0, "embedding": vector}]})
        client = OpenAIClient(api_key="sk-test-key", session=session)
        with mock.patch.dict("os.environ", OPENAI_ENV), \
                mock.patch.object(rag.ai_client, "new_client", return_value=client):
            vectors, space = rag.embed_documents(["handoff pin"])
        self.assertEqual(space, rag.SPACE_OPENAI)
        self.assertEqual(vectors[0][:2], [0.6, 0.8])  # normalised
        body = session.posts[0]["json"]
        self.assertEqual((body["input"], body["dimensions"]), (["handoff pin"], rag.REMOTE_DIM))

    def test_a_query_is_never_embedded_into_the_other_providers_space(self) -> None:
        with mock.patch.dict("os.environ", OPENAI_ENV), \
                mock.patch.object(rag, "_embed_remote", side_effect=AssertionError("must not call API")):
            self.assertIsNone(rag.embed_query("handoff pin", rag.SPACE_REMOTE))
        with mock.patch.dict("os.environ", GEMINI_ENV), \
                mock.patch.object(rag, "_embed_remote", side_effect=AssertionError("must not call API")):
            self.assertIsNone(rag.embed_query("handoff pin", rag.SPACE_OPENAI))

    def test_switching_provider_re_embeds_the_knowledge_base_once(self) -> None:
        with mock.patch.dict("os.environ", GEMINI_ENV), \
                mock.patch.object(rag, "_embed_remote", side_effect=_fake_remote_vectors):
            reseed_knowledge()
        self.assertEqual(set(KnowledgeChunk.objects.values_list("vector_space", flat=True)), {rag.SPACE_REMOTE})

        with mock.patch.dict("os.environ", OPENAI_ENV), \
                mock.patch.object(rag, "_embed_remote", side_effect=_fake_remote_vectors) as remote:
            ensure_seeded()
            self.assertEqual(set(KnowledgeChunk.objects.values_list("vector_space", flat=True)), {rag.SPACE_OPENAI})
            calls = remote.call_count
            ensure_seeded()  # already in the active space; must not re-embed
            self.assertEqual(remote.call_count, calls)

    def test_retrieval_still_answers_before_the_re_embed(self) -> None:
        # A Gemini-space corpus read under an OpenAI key: no cross-space
        # comparison and no network call, just the hashed-space fallback.
        with mock.patch.dict("os.environ", GEMINI_ENV), \
                mock.patch.object(rag, "_embed_remote", side_effect=_fake_remote_vectors):
            reseed_knowledge()
        with mock.patch.dict("os.environ", OPENAI_ENV), \
                mock.patch.object(rag, "_embed_remote", side_effect=AssertionError("must not call API")):
            results = rag.retrieve("what is the handoff PIN and when do I share it")
        self.assertTrue(results)
        self.assertEqual(results[0]["title"], "The handoff PIN")

    def test_cached_answers_from_the_other_provider_match_only_on_their_hashed_twin(self) -> None:
        with mock.patch.dict("os.environ", GEMINI_ENV), \
                mock.patch.object(rag, "_embed_remote", side_effect=_fake_remote_vectors):
            semantic_cache.store("how do handoffs work", "With a PIN.", [])
        with mock.patch.dict("os.environ", OPENAI_ENV), \
                mock.patch.object(rag, "_embed_remote", side_effect=AssertionError("must not call API")):
            hit = semantic_cache.lookup("how do handoffs work")
            miss = semantic_cache.lookup("where can I buy a bicycle")
        self.assertIsNotNone(hit)
        self.assertIsNone(miss)
