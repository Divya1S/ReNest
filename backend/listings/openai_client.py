"""OpenAI behind the interface the rest of the app already speaks.

Every AI feature in ReNest was written against two calls of the google-genai
client:

    client.models.generate_content(model=..., contents=..., config=...)
    client.models.embed_content(model=..., contents=[...], config=...)

This module offers the same two calls on top of OpenAI's Responses and
Embeddings HTTP APIs and returns objects with the attributes the callers
read (candidates, parts, function_call, finish_reason, usage_metadata,
embeddings). The assistant's tool loop, the vision helpers and the retry
logic therefore run unchanged whichever provider answers.

Why the Responses API and not Chat Completions: current OpenAI models only
combine reasoning with function calling there. Why plain HTTP and not the
SDK: the callers already own retries, time budgets and the circuit breaker,
so all that is needed is one request with a timeout, and `requests` is
already a dependency.

What is translated, in both directions:

  request                                   Responses API
  ----------------------------------------  ---------------------------------
  config.system_instruction                 instructions
  contents (user / model text)              input messages
  inline_data part (image)                  input_image part
  function_response part                    function_call_output item
  config.tools[].function_declarations      tools[] (type "function")
  tool_config mode "NONE"                   tool_choice "none"
  config.thinking_config.thinking_level     reasoning.effort
  config.max_output_tokens                  max_output_tokens (plus headroom)
  response_mime_type "application/json"     an instruction to answer in JSON
  config.response_schema                    text.format json_schema (strict)

  response                                  returned as
  ----------------------------------------  ---------------------------------
  output[].message output_text              a text part
  output[].function_call                    a function_call part
  status "incomplete" (max_output_tokens)   finish_reason "MAX_TOKENS"
  usage                                     usage_metadata
  HTTP error status                         OpenAIError with `.code`
"""

from __future__ import annotations

import json
import logging
import os
from collections import deque
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_TIMEOUT_SECONDS = 30.0
_CONNECT_TIMEOUT_SECONDS = 10.0

# Reasoning tokens count toward max_output_tokens. The budgets callers pass
# were sized for the visible answer, so each effort level adds room for the
# thinking that comes before it. Without this a short answer can come back
# empty with status "incomplete".
_REASONING_HEADROOM_TOKENS = {"none": 0, "minimal": 512, "low": 2000, "medium": 6000, "high": 12000}
_MIN_OUTPUT_TOKENS = 16

# Models documented as accepting reasoning effort "none". The others need at
# least "low". A model this list gets wrong is handled at request time: a 400
# that names the reasoning setting is retried once without it.
_NO_REASONING_MODEL_TAGS = ("luna", "terra")

_JSON_RULE = (
    "Respond with a single valid JSON value and nothing else: no code fences, "
    "no commentary before or after it."
)


class OpenAIError(Exception):
    """A failed OpenAI request. `.code` is the HTTP status, which is what the
    shared retry and failure-classification code looks at."""

    def __init__(self, code: int, message: str, *, details: Any = None, retry_after: float | None = None) -> None:
        super().__init__(f"{code} {message}")
        self.code = code
        self.message = message
        self.details = details
        self.retry_after = retry_after


# ── Response objects (the shape callers already read) ────────────────────────

@dataclass
class FunctionCall:
    name: str
    args: dict[str, Any]


@dataclass
class Part:
    text: str | None = None
    function_call: FunctionCall | None = None


@dataclass
class Content:
    """One model turn. Callers append it to `contents` as is, so it keeps the
    raw output items: reasoning and function-call items have to be sent back
    exactly as they were returned for the next request to be accepted."""

    role: str
    parts: list[Part]
    raw_output: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Candidate:
    content: Content
    finish_reason: str


@dataclass
class Usage:
    prompt_token_count: int
    candidates_token_count: int
    thoughts_token_count: int
    total_token_count: int


@dataclass
class GenerateResponse:
    candidates: list[Candidate]
    usage_metadata: Usage

    @property
    def text(self) -> str:
        return "\n".join(part.text for part in self.candidates[0].content.parts if part.text)


@dataclass
class Embedding:
    values: list[float]


@dataclass
class EmbedResponse:
    embeddings: list[Embedding]


# ── Request translation ──────────────────────────────────────────────────────

def reasoning_effort(model: str, thinking_level: str | None) -> str:
    """Map the caller's thinking level to an effort this model accepts.

    "low" means "answer as fast as you can": no reasoning where the model
    allows that. "high" is capped at medium because every call here runs
    inside a web request with a time budget. No stated level gets "low".
    """
    if thinking_level == "low":
        return "none" if any(tag in model for tag in _NO_REASONING_MODEL_TAGS) else "low"
    if thinking_level == "high":
        return "medium"
    return "low"


def _instructions(config: dict[str, Any]) -> str:
    parts = [str(config.get("system_instruction") or "").strip()]
    if config.get("response_mime_type") == "application/json":
        parts.append(_JSON_RULE)
    return "\n\n".join(part for part in parts if part)


# ── Answer schemas ───────────────────────────────────────────────────────────

_SCHEMA_TYPES = frozenset({"object", "array", "string", "number", "integer", "boolean"})
# Strict mode needs an object at the root. A schema with any other root (the
# app's are mostly arrays) is sent inside an object under this key, and the
# answer is taken back out before the caller sees it.
_ANSWER_KEY = "result"


def _or_null(schema: dict[str, Any]) -> dict[str, Any]:
    kinds = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
    if "null" in kinds:
        return schema
    optional = {**schema, "type": [*kinds, "null"]}
    if "enum" in optional:
        optional["enum"] = [*optional["enum"], None]
    return optional


def strict_schema(schema: Any) -> dict[str, Any]:
    """An answer schema in the form OpenAI's strict mode accepts.

    Callers write schemas the way google-genai takes them: upper-case type
    names, optional properties, `nullable`. Strict mode wants plain JSON
    Schema in which every object lists all of its properties as required and
    allows no others, so an optional property becomes "this type or null".
    Keywords outside that core are left out: the prompt still states them
    and callers validate what comes back anyway.

    Raises ValueError for a schema it cannot express.
    """
    if not isinstance(schema, dict):
        raise ValueError("schema must be an object")
    kind = str(schema.get("type") or "").lower()
    if kind not in _SCHEMA_TYPES:
        raise ValueError(f"unsupported schema type: {schema.get('type')!r}")
    strict: dict[str, Any] = {"type": kind}
    for keyword in ("description", "enum"):
        if keyword in schema:
            strict[keyword] = schema[keyword]
    if kind == "object":
        properties = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        strict["properties"] = {
            name: strict_schema(sub) if name in required else _or_null(strict_schema(sub))
            for name, sub in properties.items()
        }
        strict["required"] = list(properties)
        strict["additionalProperties"] = False
    elif kind == "array":
        strict["items"] = strict_schema(schema.get("items"))
    return _or_null(strict) if schema.get("nullable") else strict


def answer_format(config: dict[str, Any]) -> tuple[dict[str, Any] | None, bool]:
    """(the `text.format` for this request or None, whether the answer is wrapped).

    With a format the provider only produces JSON of that exact shape. A
    schema that cannot be expressed is left to the prompt and the JSON rule.
    """
    schema = config.get("response_schema")
    if config.get("response_mime_type") != "application/json" or schema is None:
        return None, False
    try:
        strict = strict_schema(schema)
    except ValueError:
        logger.warning("Answer schema could not be made strict; relying on the prompt")
        return None, False
    wrapped = strict["type"] != "object"
    if wrapped:
        strict = {
            "type": "object",
            "properties": {_ANSWER_KEY: strict},
            "required": [_ANSWER_KEY],
            "additionalProperties": False,
        }
    return {"type": "json_schema", "name": "answer", "strict": True, "schema": strict}, wrapped


def unwrap_answer(response: "GenerateResponse") -> None:
    """Take a wrapped answer (see _ANSWER_KEY) back out, in place."""
    for part in response.candidates[0].content.parts:
        if not part.text:
            continue
        try:
            value = json.loads(part.text)
        except ValueError:
            continue  # clipped mid-answer; the caller sees MAX_TOKENS and retries
        if isinstance(value, dict) and _ANSWER_KEY in value:
            part.text = json.dumps(value[_ANSWER_KEY])


def _tools(config: dict[str, Any]) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for group in config.get("tools") or []:
        for declaration in (group or {}).get("function_declarations") or []:
            tools.append(
                {
                    "type": "function",
                    "name": declaration["name"],
                    "description": declaration.get("description", ""),
                    "parameters": declaration.get("parameters") or {"type": "object", "properties": {}},
                    # The declarations have optional fields, which strict mode
                    # forbids. Handlers validate their arguments themselves.
                    "strict": False,
                }
            )
    return tools


def _tools_disabled(config: dict[str, Any]) -> bool:
    mode = ((config.get("tool_config") or {}).get("function_calling_config") or {}).get("mode")
    return str(mode or "").upper() == "NONE"


def _take_call_id(pending: deque[tuple[str, str]], name: str) -> str | None:
    """The call a tool result answers: the oldest unanswered call of that name."""
    for index, (call_id, call_name) in enumerate(pending):
        if call_name == name:
            del pending[index]
            return call_id
    if pending:
        return pending.popleft()[0]
    return None


def _message_content(role: str, parts: list[dict[str, Any]]) -> Any:
    if role == "assistant" or all(part["type"] == "input_text" for part in parts):
        return "\n".join(part["text"] for part in parts if part["type"] == "input_text")
    return parts


def to_input_items(contents: list[Any]) -> list[dict[str, Any]]:
    """Translate the conversation into Responses API input items."""
    items: list[dict[str, Any]] = []
    pending: deque[tuple[str, str]] = deque()  # (call_id, tool name) waiting for a result
    for entry in contents:
        if isinstance(entry, Content):
            items.extend(entry.raw_output)
            pending.extend(
                (str(item.get("call_id")), str(item.get("name", "")))
                for item in entry.raw_output
                if item.get("type") == "function_call"
            )
            continue
        role = "assistant" if entry.get("role") == "model" else "user"
        message: list[dict[str, Any]] = []

        def flush() -> None:
            if message:
                items.append({"role": role, "content": _message_content(role, list(message))})
                message.clear()

        for part in entry.get("parts") or []:
            if part.get("function_response") is not None:
                result = part["function_response"]
                name = str(result.get("name", ""))
                output = json.dumps(result.get("response"), default=str)
                call_id = _take_call_id(pending, name)
                if call_id is None:
                    # No call to attach it to: keep the information as text.
                    message.append({"type": "input_text", "text": f"Result of {name}: {output}"})
                else:
                    flush()
                    items.append({"type": "function_call_output", "call_id": call_id, "output": output})
            elif part.get("inline_data") is not None:
                image = part["inline_data"]
                message.append(
                    {
                        "type": "input_image",
                        "image_url": f"data:{image.get('mime_type', 'image/jpeg')};base64,{image.get('data', '')}",
                        "detail": "high",
                    }
                )
            elif part.get("text"):
                message.append({"type": "input_text", "text": str(part["text"])})
        flush()
    return items


def build_request(model: str, contents: list[Any], config: dict[str, Any] | None) -> dict[str, Any]:
    """The Responses API request body for one generate_content call."""
    config = dict(config or {})
    effort = reasoning_effort(model, (config.get("thinking_config") or {}).get("thinking_level"))
    body: dict[str, Any] = {
        "model": model,
        "input": to_input_items(contents),
        # Nothing here continues a stored conversation: every request carries
        # the whole history, so OpenAI does not need to keep a copy.
        "store": False,
        # With nothing stored, a reasoning item can only be sent back (as the
        # tool loop must) if it carries its own encrypted content.
        "include": ["reasoning.encrypted_content"],
        "reasoning": {"effort": effort},
    }
    instructions = _instructions(config)
    if instructions:
        body["instructions"] = instructions
    text_format, _ = answer_format(config)
    if text_format:
        body["text"] = {"format": text_format}
    tools = _tools(config)
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "none" if _tools_disabled(config) else "auto"
    if config.get("max_output_tokens"):
        body["max_output_tokens"] = max(
            _MIN_OUTPUT_TOKENS, int(config["max_output_tokens"]) + _REASONING_HEADROOM_TOKENS[effort]
        )
    return body


# ── Response translation ─────────────────────────────────────────────────────

def _positive_int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def parse_response(payload: dict[str, Any]) -> GenerateResponse:
    """A Responses API result as the object callers expect."""
    if payload.get("status") == "failed":
        error = payload.get("error") or {}
        raise OpenAIError(502, str(error.get("message") or "The model run failed."), details=payload)

    output = [item for item in payload.get("output") or [] if isinstance(item, dict)]
    parts: list[Part] = []
    for item in output:
        if item.get("type") == "message":
            for block in item.get("content") or []:
                if block.get("type") == "output_text" and block.get("text"):
                    parts.append(Part(text=str(block["text"])))
                elif block.get("type") == "refusal" and block.get("refusal"):
                    parts.append(Part(text=str(block["refusal"])))
        elif item.get("type") == "function_call" and item.get("name"):
            try:
                arguments = json.loads(item.get("arguments") or "{}")
            except ValueError:
                arguments = {}
            parts.append(
                Part(function_call=FunctionCall(str(item["name"]), arguments if isinstance(arguments, dict) else {}))
            )

    finish_reason = "STOP"
    if payload.get("status") == "incomplete":
        reason = str((payload.get("incomplete_details") or {}).get("reason") or "unknown")
        finish_reason = "MAX_TOKENS" if reason == "max_output_tokens" else f"INCOMPLETE_{reason.upper()}"

    usage = payload.get("usage") or {}
    reasoning_tokens = _positive_int((usage.get("output_tokens_details") or {}).get("reasoning_tokens"))
    output_tokens = _positive_int(usage.get("output_tokens"))
    return GenerateResponse(
        candidates=[Candidate(Content("model", parts, raw_output=output), finish_reason)],
        usage_metadata=Usage(
            prompt_token_count=_positive_int(usage.get("input_tokens")),
            candidates_token_count=max(0, output_tokens - reasoning_tokens),
            thoughts_token_count=reasoning_tokens,
            total_token_count=_positive_int(usage.get("total_tokens")),
        ),
    )


# ── HTTP ─────────────────────────────────────────────────────────────────────

def _request_timeout(config: dict[str, Any] | None, default: float) -> float:
    """Per-request timeout in seconds; callers pass it in milliseconds."""
    millis = ((config or {}).get("http_options") or {}).get("timeout")
    return max(1.0, millis / 1000) if isinstance(millis, (int, float)) and millis > 0 else default


def _retry_after_seconds(headers: Any) -> float | None:
    try:
        return max(0.0, float(headers.get("Retry-After")))
    except (TypeError, ValueError):
        return None


class _Models:
    def __init__(self, client: "OpenAIClient") -> None:
        self._client = client

    def generate_content(self, *, model: str, contents: list[Any], config: dict[str, Any] | None = None) -> GenerateResponse:
        timeout = _request_timeout(config, self._client.timeout_seconds)
        body = build_request(model, contents, config)
        try:
            payload = self._client.post("/responses", body, timeout)
        except OpenAIError as exc:
            # A model that does not take this reasoning setting says so with a
            # 400. Its own default is then the right choice.
            if exc.code != 400 or "reasoning" not in exc.message.lower():
                raise
            logger.warning("OpenAI model %s rejected reasoning effort %s; using its default", model, body["reasoning"])
            without_effort = {key: value for key, value in body.items() if key != "reasoning"}
            payload = self._client.post("/responses", without_effort, timeout)
        response = parse_response(payload)
        if answer_format(config or {})[1]:
            unwrap_answer(response)
        return response

    def embed_content(self, *, model: str, contents: list[str], config: dict[str, Any] | None = None) -> EmbedResponse:
        body: dict[str, Any] = {"model": model, "input": list(contents), "encoding_format": "float"}
        dimensions = (config or {}).get("output_dimensionality")
        if dimensions:
            body["dimensions"] = int(dimensions)
        payload = self._client.post("/embeddings", body, _request_timeout(config, self._client.timeout_seconds))
        rows = sorted(payload.get("data") or [], key=lambda row: row.get("index", 0))
        return EmbedResponse([Embedding(values=list(row.get("embedding") or [])) for row in rows])


class OpenAIClient:
    """The two calls the app makes, on top of the OpenAI HTTP API."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        session: Any = None,
    ) -> None:
        import requests

        self._api_key = (api_key if api_key is not None else os.environ.get("OPENAI_API_KEY", "")).strip()
        self._base_url = (base_url or os.environ.get("OPENAI_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.timeout_seconds = timeout_seconds
        # One session per client keeps the connection open between the calls
        # of a turn. Tests pass their own to script the HTTP exchange.
        self._session = session if session is not None else requests.Session()
        self.models = _Models(self)

    def post(self, path: str, body: dict[str, Any], timeout: float) -> dict[str, Any]:
        import requests

        try:
            response = self._session.post(
                f"{self._base_url}{path}",
                json=body,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=(min(_CONNECT_TIMEOUT_SECONDS, timeout), timeout),
            )
        # Re-raised as the builtin types the shared failure handling knows.
        # The original error text is left out: it can quote request headers.
        except requests.Timeout:
            raise TimeoutError("The OpenAI request timed out.") from None
        except requests.RequestException as exc:
            raise ConnectionError(f"Could not reach OpenAI ({type(exc).__name__}).") from None

        if response.status_code >= 400:
            raise self._error(response)
        try:
            payload = response.json()
        except ValueError:
            raise OpenAIError(502, "OpenAI returned a response that is not JSON.") from None
        if not isinstance(payload, dict):
            raise OpenAIError(502, "OpenAI returned an unexpected response.")
        return payload

    @staticmethod
    def _error(response: Any) -> OpenAIError:
        try:
            payload = response.json()
        except ValueError:
            payload = None
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            label = error.get("code") or error.get("type") or "error"
            message = f"{label}: {error.get('message') or 'request failed'}"
        else:
            message = "request failed"
        return OpenAIError(
            response.status_code,
            message[:500],
            details=payload,
            retry_after=_retry_after_seconds(response.headers),
        )
