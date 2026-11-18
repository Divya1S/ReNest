"""The OpenAI adapter and the provider switch.

Nothing here reaches the network: the HTTP session is replaced with a fake
that returns scripted OpenAI payloads, so these tests pin down exactly what
is sent to OpenAI and how its answers are read.
"""

from __future__ import annotations

import base64
import json
from typing import Any
from unittest import mock

import requests
from django.test import SimpleTestCase

from dormcycle.env import ai_model_default, ai_provider
from listings import ai_client, openai_client
from listings.openai_client import OpenAIClient, OpenAIError

OPENAI_ENV = {"OPENAI_API_KEY": "sk-test-key", "GEMINI_API_KEY": "", "GOOGLE_API_KEY": "", "AI_PROVIDER": ""}


class FakeHttpResponse:
    def __init__(self, payload: Any, status: int = 200, headers: dict[str, str] | None = None) -> None:
        self._payload = payload
        self.status_code = status
        self.headers = headers or {}

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    """Stands in for requests.Session: records each POST, replays a script."""

    def __init__(self, *script: Any) -> None:
        self.script = list(script)
        self.posts: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> FakeHttpResponse:
        self.posts.append({"url": url, **kwargs})
        if not self.script:
            raise AssertionError("FakeSession script exhausted")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item if isinstance(item, FakeHttpResponse) else FakeHttpResponse(item)


def client_with(*script: Any) -> tuple[OpenAIClient, FakeSession]:
    session = FakeSession(*script)
    return OpenAIClient(api_key="sk-test-key", session=session), session


def text_payload(text: str, **extra: Any) -> dict[str, Any]:
    return {
        "status": "completed",
        "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}],
        "usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
        **extra,
    }


def user(text: str) -> dict[str, Any]:
    return {"role": "user", "parts": [{"text": text}]}


class ProviderSelectionTests(SimpleTestCase):
    def test_no_key_means_no_provider(self) -> None:
        self.assertEqual(ai_provider({}), "")
        self.assertEqual(ai_provider({"OPENAI_API_KEY": "   "}), "")

    def test_one_key_selects_its_provider(self) -> None:
        self.assertEqual(ai_provider({"OPENAI_API_KEY": "k"}), "openai")
        self.assertEqual(ai_provider({"GEMINI_API_KEY": "k"}), "gemini")
        self.assertEqual(ai_provider({"GOOGLE_API_KEY": "k"}), "gemini")

    def test_openai_wins_when_both_keys_are_set(self) -> None:
        self.assertEqual(ai_provider({"OPENAI_API_KEY": "k", "GEMINI_API_KEY": "k"}), "openai")

    def test_ai_provider_forces_a_choice(self) -> None:
        both = {"OPENAI_API_KEY": "k", "GEMINI_API_KEY": "k"}
        self.assertEqual(ai_provider({**both, "AI_PROVIDER": "gemini"}), "gemini")
        self.assertEqual(ai_provider({**both, "AI_PROVIDER": " OpenAI "}), "openai")

    def test_a_forced_provider_without_its_key_is_off_not_swapped(self) -> None:
        # Asking for OpenAI and silently getting Gemini would send data to a
        # provider the operator did not choose.
        self.assertEqual(ai_provider({"AI_PROVIDER": "openai", "GEMINI_API_KEY": "k"}), "")
        self.assertEqual(ai_provider({"AI_PROVIDER": "claude", "OPENAI_API_KEY": "k"}), "")

    def test_model_defaults_follow_the_provider(self) -> None:
        self.assertTrue(ai_model_default("AI_MODEL_FAST", {"OPENAI_API_KEY": "k"}).startswith("gpt-"))
        self.assertTrue(ai_model_default("AI_MODEL_EMBEDDING", {"OPENAI_API_KEY": "k"}).startswith("text-embedding"))
        self.assertTrue(ai_model_default("AI_MODEL_FAST", {"GEMINI_API_KEY": "k"}).startswith("gemini-"))
        self.assertTrue(ai_model_default("AI_MODEL_FAST", {}).startswith("gemini-"))

    def test_ai_client_reports_the_environment(self) -> None:
        with mock.patch.dict("os.environ", OPENAI_ENV):
            self.assertEqual(ai_client.provider(), "openai")
            self.assertTrue(ai_client.ai_available())
            self.assertIsInstance(ai_client.new_client(), OpenAIClient)
        with mock.patch.dict("os.environ", {**OPENAI_ENV, "OPENAI_API_KEY": ""}):
            self.assertEqual(ai_client.provider(), "")
            self.assertFalse(ai_client.ai_available())
            with self.assertRaises(ai_client.AiNotConfigured):
                ai_client.generate_text("hello")


class RequestTranslationTests(SimpleTestCase):
    def test_plain_text_request(self) -> None:
        body = openai_client.build_request(
            "gpt-6-luna",
            [user("hello")],
            {"system_instruction": "Be brief.", "max_output_tokens": 300, "thinking_config": {"thinking_level": "low"}},
        )
        self.assertEqual(body["model"], "gpt-6-luna")
        self.assertEqual(body["instructions"], "Be brief.")
        self.assertEqual(body["input"], [{"role": "user", "content": "hello"}])
        self.assertIs(body["store"], False)
        self.assertEqual(body["reasoning"], {"effort": "none"})
        self.assertEqual(body["max_output_tokens"], 300)
        self.assertNotIn("tools", body)

    def test_history_roles_are_mapped(self) -> None:
        contents = [user("hi"), {"role": "model", "parts": [{"text": "Hello!"}]}, user("find a lamp")]
        self.assertEqual(
            openai_client.to_input_items(contents),
            [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "Hello!"},
                {"role": "user", "content": "find a lamp"},
            ],
        )

    def test_image_becomes_a_data_url(self) -> None:
        data = base64.b64encode(b"fake-jpeg").decode()
        contents = [{"role": "user", "parts": [{"inline_data": {"mime_type": "image/jpeg", "data": data}}, {"text": "What is this?"}]}]
        (message,) = openai_client.to_input_items(contents)
        self.assertEqual(
            message["content"],
            [
                {"type": "input_image", "image_url": f"data:image/jpeg;base64,{data}", "detail": "high"},
                {"type": "input_text", "text": "What is this?"},
            ],
        )

    def test_tools_are_flattened_and_not_strict(self) -> None:
        declaration = {
            "name": "search_listings",
            "description": "Search live listings.",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
        }
        body = openai_client.build_request("gpt-6.2-sol", [user("lamps")], {"tools": [{"function_declarations": [declaration]}]})
        self.assertEqual(body["tools"], [{"type": "function", **declaration, "strict": False}])
        self.assertEqual(body["tool_choice"], "auto")

    def test_function_calling_mode_none_keeps_tools_but_forbids_calls(self) -> None:
        config = {
            "tools": [{"function_declarations": [{"name": "search_help"}]}],
            "tool_config": {"function_calling_config": {"mode": "NONE"}},
        }
        body = openai_client.build_request("gpt-6.2-sol", [user("hi")], config)
        self.assertEqual(body["tool_choice"], "none")
        self.assertEqual(body["tools"][0]["parameters"], {"type": "object", "properties": {}})

    def test_json_requests_carry_the_rule(self) -> None:
        body = openai_client.build_request(
            "gpt-6-luna",
            [user("rank these")],
            {"system_instruction": "You rank items.", "response_mime_type": "application/json"},
        )
        self.assertTrue(body["instructions"].startswith("You rank items.\n\nRespond with a single valid JSON value"))
        # No schema, no format: these answers can be arrays, which JSON mode refuses.
        self.assertNotIn("text", body)

    def test_an_object_schema_becomes_a_strict_format(self) -> None:
        schema = {
            "type": "OBJECT",
            "properties": {
                "status": {"type": "STRING", "enum": ["approved", "flagged"]},
                "reason": {"type": "STRING"},
                "note": {"type": "STRING"},
            },
            "required": ["status", "reason"],
        }
        body = openai_client.build_request(
            "gpt-6-luna", [user("check this")], {"response_mime_type": "application/json", "response_schema": schema}
        )
        self.assertEqual(
            body["text"]["format"],
            {
                "type": "json_schema",
                "name": "answer",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string", "enum": ["approved", "flagged"]},
                        "reason": {"type": "string"},
                        # Optional in the caller's schema: strict mode wants
                        # every property listed, so it may be null instead.
                        "note": {"type": ["string", "null"]},
                    },
                    "required": ["status", "reason", "note"],
                    "additionalProperties": False,
                },
            },
        )

    def test_an_array_schema_is_wrapped_in_an_object(self) -> None:
        schema = {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"id": {"type": "INTEGER"}}, "required": ["id"]}}
        body = openai_client.build_request(
            "gpt-6-luna", [user("rank these")], {"response_mime_type": "application/json", "response_schema": schema}
        )
        strict = body["text"]["format"]["schema"]
        self.assertEqual((strict["type"], strict["required"]), ("object", ["result"]))
        self.assertEqual(strict["properties"]["result"]["type"], "array")
        self.assertIs(strict["properties"]["result"]["items"]["additionalProperties"], False)

    def test_a_schema_that_cannot_be_made_strict_is_left_to_the_prompt(self) -> None:
        config = {"response_mime_type": "application/json", "response_schema": {"type": "ARRAY"}}
        with self.assertLogs("listings.openai_client", level="WARNING"):
            body = openai_client.build_request("gpt-6-luna", [user("rank these")], config)
        self.assertNotIn("text", body)

    def test_reasoning_effort_per_model_and_level(self) -> None:
        cases = [
            ("gpt-6-luna", "low", "none"),
            ("gpt-6.1-terra", "low", "none"),
            ("gpt-6.2-sol", "low", "low"),
            ("gpt-6.2-sol", None, "low"),
            ("gpt-6.2-sol", "high", "medium"),
        ]
        for model, level, expected in cases:
            with self.subTest(model=model, level=level):
                self.assertEqual(openai_client.reasoning_effort(model, level), expected)

    def test_output_budget_gets_room_for_reasoning(self) -> None:
        low = openai_client.build_request("gpt-6.2-sol", [user("hi")], {"max_output_tokens": 500})
        self.assertEqual(low["max_output_tokens"], 2500)
        none = openai_client.build_request(
            "gpt-6-luna", [user("hi")], {"max_output_tokens": 500, "thinking_config": {"thinking_level": "low"}}
        )
        self.assertEqual(none["max_output_tokens"], 500)
        self.assertNotIn("max_output_tokens", openai_client.build_request("gpt-6-luna", [user("hi")], {}))


class ResponseTranslationTests(SimpleTestCase):
    def test_text_and_usage(self) -> None:
        payload = text_payload(
            "Hello there.",
            usage={"input_tokens": 120, "output_tokens": 50, "output_tokens_details": {"reasoning_tokens": 30}, "total_tokens": 170},
        )
        response = openai_client.parse_response(payload)
        self.assertEqual(ai_client.extract_text(response), "Hello there.")
        self.assertEqual(response.text, "Hello there.")
        self.assertEqual(response.candidates[0].finish_reason, "STOP")
        usage = response.usage_metadata
        self.assertEqual(
            (usage.prompt_token_count, usage.candidates_token_count, usage.thoughts_token_count, usage.total_token_count),
            (120, 20, 30, 170),
        )

    def test_function_calls_are_parsed(self) -> None:
        payload = {
            "status": "completed",
            "output": [
                {"type": "reasoning", "id": "rs_1", "encrypted_content": "opaque"},
                {"type": "function_call", "call_id": "call_1", "name": "search_listings", "arguments": '{"query": "lamp"}'},
                {"type": "function_call", "call_id": "call_2", "name": "get_my_activity", "arguments": "not json"},
            ],
        }
        response = openai_client.parse_response(payload)
        calls = [part.function_call for part in response.candidates[0].content.parts]
        self.assertEqual([(call.name, call.args) for call in calls if call], [("search_listings", {"query": "lamp"}), ("get_my_activity", {})])
        self.assertEqual(ai_client.extract_text(response), "")

    def test_a_clipped_answer_reads_as_max_tokens(self) -> None:
        payload = text_payload("Half an ans", status="incomplete", incomplete_details={"reason": "max_output_tokens"})
        response = openai_client.parse_response(payload)
        self.assertTrue(ai_client._hit_token_cap(response))
        filtered = openai_client.parse_response(text_payload("", status="incomplete", incomplete_details={"reason": "content_filter"}))
        self.assertFalse(ai_client._hit_token_cap(filtered))

    def test_a_failed_run_raises_a_provider_error(self) -> None:
        with self.assertRaises(OpenAIError) as caught:
            openai_client.parse_response({"status": "failed", "error": {"message": "server exploded"}})
        self.assertEqual(caught.exception.code, 502)


class ToolLoopRoundTripTests(SimpleTestCase):
    """The model turn is echoed back verbatim, then each tool result is
    attached to the call it answers."""

    def model_turn(self) -> openai_client.Content:
        payload = {
            "status": "completed",
            "output": [
                {"type": "reasoning", "id": "rs_1", "encrypted_content": "opaque"},
                {"type": "function_call", "id": "fc_1", "call_id": "call_a", "name": "search_listings", "arguments": '{"query": "lamp"}'},
                {"type": "function_call", "id": "fc_2", "call_id": "call_b", "name": "search_listings", "arguments": '{"query": "desk"}'},
                {"type": "function_call", "id": "fc_3", "call_id": "call_c", "name": "get_my_activity", "arguments": "{}"},
            ],
        }
        return openai_client.parse_response(payload).candidates[0].content

    def test_results_follow_the_calls_they_answer(self) -> None:
        turn = self.model_turn()
        results = {
            "role": "user",
            "parts": [
                {"function_response": {"name": "search_listings", "response": {"ok": True, "items": ["lamp"]}}},
                {"function_response": {"name": "search_listings", "response": {"ok": True, "items": ["desk"]}}},
                {"function_response": {"name": "get_my_activity", "response": {"ok": True}}},
            ],
        }
        items = openai_client.to_input_items([user("find things"), turn, results])
        self.assertEqual(items[0], {"role": "user", "content": "find things"})
        # Reasoning and call items go back exactly as they came, in order.
        self.assertEqual(items[1:5], turn.raw_output)
        self.assertEqual(
            items[5:],
            [
                {"type": "function_call_output", "call_id": "call_a", "output": json.dumps({"ok": True, "items": ["lamp"]})},
                {"type": "function_call_output", "call_id": "call_b", "output": json.dumps({"ok": True, "items": ["desk"]})},
                {"type": "function_call_output", "call_id": "call_c", "output": json.dumps({"ok": True})},
            ],
        )

    def test_results_are_matched_by_tool_name_when_out_of_order(self) -> None:
        results = {
            "role": "user",
            "parts": [
                {"function_response": {"name": "get_my_activity", "response": {"n": 1}}},
                {"function_response": {"name": "search_listings", "response": {"n": 2}}},
            ],
        }
        items = openai_client.to_input_items([self.model_turn(), results])
        self.assertEqual([item["call_id"] for item in items[4:]], ["call_c", "call_a"])

    def test_a_result_with_no_call_is_kept_as_text(self) -> None:
        orphan = {"role": "user", "parts": [{"function_response": {"name": "search_help", "response": {"ok": True}}}]}
        self.assertEqual(
            openai_client.to_input_items([orphan]),
            [{"role": "user", "content": 'Result of search_help: {"ok": true}'}],
        )


class HttpTests(SimpleTestCase):
    def test_request_goes_to_the_responses_endpoint_with_the_key(self) -> None:
        client, session = client_with(text_payload("ok"))
        response = client.models.generate_content(
            model="gpt-6-luna", contents=[user("hi")], config={"http_options": {"timeout": 12_000}}
        )
        self.assertEqual(response.text, "ok")
        (post,) = session.posts
        self.assertEqual(post["url"], "https://api.openai.com/v1/responses")
        self.assertEqual(post["headers"], {"Authorization": "Bearer sk-test-key"})
        self.assertEqual(post["timeout"], (10.0, 12.0))
        self.assertEqual(post["json"]["input"], [{"role": "user", "content": "hi"}])

    def test_base_url_can_be_overridden(self) -> None:
        session = FakeSession(text_payload("ok"))
        with mock.patch.dict("os.environ", {"OPENAI_BASE_URL": "https://gateway.example/v1/"}):
            client = OpenAIClient(api_key="k", session=session)
        client.models.generate_content(model="m", contents=[user("hi")])
        self.assertEqual(session.posts[0]["url"], "https://gateway.example/v1/responses")

    def test_http_errors_carry_the_status_code(self) -> None:
        error = {"error": {"message": "You exceeded your current quota.", "type": "insufficient_quota", "code": "insufficient_quota"}}
        client, _ = client_with(FakeHttpResponse(error, status=429, headers={"Retry-After": "1.5"}))
        with self.assertRaises(OpenAIError) as caught:
            client.models.generate_content(model="m", contents=[user("hi")])
        self.assertEqual(caught.exception.code, 429)
        self.assertEqual(caught.exception.retry_after, 1.5)
        self.assertIn("insufficient_quota", str(caught.exception))
        self.assertEqual(ai_client._server_retry_hint(caught.exception), 1.5)

    def test_an_error_page_that_is_not_json_still_raises_cleanly(self) -> None:
        client, _ = client_with(FakeHttpResponse(ValueError("no json"), status=503))
        with self.assertRaises(OpenAIError) as caught:
            client.models.generate_content(model="m", contents=[user("hi")])
        self.assertEqual(caught.exception.code, 503)

    def test_timeouts_and_network_errors_become_builtin_errors(self) -> None:
        # The shared failure handling recognises the builtin types, and the
        # message must not repeat request details (they include the key).
        client, _ = client_with(requests.Timeout("read timed out, headers={'Authorization': 'Bearer sk-test-key'}"))
        with self.assertRaises(TimeoutError) as timed_out:
            client.models.generate_content(model="m", contents=[user("hi")])
        self.assertNotIn("sk-test-key", str(timed_out.exception))

        client, _ = client_with(requests.ConnectionError("dns failure for sk-test-key"))
        with self.assertRaises(ConnectionError) as refused:
            client.models.generate_content(model="m", contents=[user("hi")])
        self.assertNotIn("sk-test-key", str(refused.exception))

    def test_a_rejected_reasoning_setting_is_retried_without_it(self) -> None:
        rejection = FakeHttpResponse(
            {"error": {"message": "Unsupported value: 'reasoning.effort' does not support 'none' with this model.", "type": "invalid_request_error"}},
            status=400,
        )
        client, session = client_with(rejection, text_payload("ok"))
        with self.assertLogs("listings.openai_client", level="WARNING"):
            response = client.models.generate_content(
                model="gpt-6-luna", contents=[user("hi")], config={"thinking_config": {"thinking_level": "low"}}
            )
        self.assertEqual(response.text, "ok")
        self.assertIn("reasoning", session.posts[0]["json"])
        self.assertNotIn("reasoning", session.posts[1]["json"])

    def test_other_bad_requests_are_not_retried(self) -> None:
        client, session = client_with(FakeHttpResponse({"error": {"message": "Invalid model.", "type": "invalid_request_error"}}, status=400))
        with self.assertRaises(OpenAIError):
            client.models.generate_content(model="nope", contents=[user("hi")])
        self.assertEqual(len(session.posts), 1)

    def test_embeddings_keep_input_order(self) -> None:
        payload = {"data": [{"index": 1, "embedding": [0.0, 1.0]}, {"index": 0, "embedding": [1.0, 0.0]}]}
        client, session = client_with(payload)
        response = client.models.embed_content(
            model="text-embedding-3-small",
            contents=["first", "second"],
            config={"task_type": "RETRIEVAL_DOCUMENT", "output_dimensionality": 768},
        )
        self.assertEqual([embedding.values for embedding in response.embeddings], [[1.0, 0.0], [0.0, 1.0]])
        (post,) = session.posts
        self.assertEqual(post["url"], "https://api.openai.com/v1/embeddings")
        self.assertEqual(
            post["json"],
            {"model": "text-embedding-3-small", "input": ["first", "second"], "encoding_format": "float", "dimensions": 768},
        )


class SharedHelpersOverOpenAITests(SimpleTestCase):
    """The marketplace helpers (text, JSON, vision) through the real adapter."""

    def setUp(self) -> None:
        self.adapter, self.session = client_with()
        for patcher in (
            mock.patch.dict("os.environ", OPENAI_ENV),
            mock.patch.object(ai_client, "_client", return_value=self.adapter),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_generate_json_parses_the_answer(self) -> None:
        self.session.script = [text_payload('```json\n{"category": "furniture"}\n```')]
        result = ai_client.generate_json("Pick a category for: desk lamp", model="gpt-6-luna")
        self.assertEqual(result, {"category": "furniture"})
        self.assertIn("single valid JSON value", self.session.posts[0]["json"]["instructions"])

    def test_an_array_answer_comes_back_unwrapped(self) -> None:
        self.session.script = [text_payload('{"result": [4, 10]}')]
        result = ai_client.generate_json(
            "Which ids match?", model="gpt-6-luna", response_schema={"type": "ARRAY", "items": {"type": "INTEGER"}}
        )
        self.assertEqual(result, [4, 10])
        self.assertEqual(self.session.posts[0]["json"]["text"]["format"]["schema"]["required"], ["result"])

    def test_an_object_answer_is_returned_as_is(self) -> None:
        self.session.script = [text_payload('{"status": "approved", "reason": "fine", "result": "kept"}')]
        schema = {
            "type": "OBJECT",
            "properties": {"status": {"type": "STRING"}, "reason": {"type": "STRING"}, "result": {"type": "STRING"}},
            "required": ["status", "reason", "result"],
        }
        result = ai_client.generate_json("Check this.", model="gpt-6-luna", response_schema=schema)
        self.assertEqual(result, {"status": "approved", "reason": "fine", "result": "kept"})

    def test_a_schema_the_provider_refuses_falls_back_to_the_prompt(self) -> None:
        self.session.script = [
            FakeHttpResponse({"error": {"message": "Invalid schema for response_format 'answer'.", "type": "invalid_request_error"}}, status=400),
            text_payload("[4, 10]"),
        ]
        result = ai_client.generate_json(
            "Which ids match?", model="gpt-6-luna", response_schema={"type": "ARRAY", "items": {"type": "INTEGER"}}
        )
        self.assertEqual(result, [4, 10])
        self.assertIn("text", self.session.posts[0]["json"])
        self.assertNotIn("text", self.session.posts[1]["json"])

    def test_generate_vision_json_sends_the_image(self) -> None:
        self.session.script = [text_payload('[{"name": "Desk lamp", "confidence": 0.9}]')]
        result = ai_client.generate_vision_json(b"jpeg-bytes", "image/jpeg", "List the items.", model="gpt-6.2-sol")
        self.assertEqual(result, [{"name": "Desk lamp", "confidence": 0.9}])
        content = self.session.posts[0]["json"]["input"][0]["content"]
        self.assertEqual(content[0]["type"], "input_image")
        self.assertTrue(content[0]["image_url"].startswith("data:image/jpeg;base64,"))

    def test_a_clipped_answer_is_asked_again_with_a_bigger_budget(self) -> None:
        self.session.script = [
            text_payload("The lamp is", status="incomplete", incomplete_details={"reason": "max_output_tokens"}),
            text_payload("The lamp is in good condition."),
        ]
        text = ai_client.generate_text("Describe the lamp", max_tokens=100, model="gpt-6-luna")
        self.assertEqual(text, "The lamp is in good condition.")
        first, second = (post["json"]["max_output_tokens"] for post in self.session.posts)
        self.assertGreater(second, first)

    def test_a_rate_limit_is_retried_once(self) -> None:
        self.session.script = [
            FakeHttpResponse({"error": {"message": "Rate limit reached.", "type": "rate_limit_error"}}, status=429, headers={"Retry-After": "0"}),
            text_payload("done"),
        ]
        with mock.patch.object(ai_client.time, "sleep") as sleep, self.assertLogs("listings.ai_client", level="WARNING"):
            self.assertEqual(ai_client.generate_text("hi", model="gpt-6-luna"), "done")
        sleep.assert_called_once_with(0.0)
        self.assertEqual(len(self.session.posts), 2)
