# Nest Concierge — AI assistant architecture

The concierge is ReNest's **single assistant**, powered by **Google Gemini**. It answers from
live marketplace data and a built-in knowledge base, renders real ReNest components inside the
chat (generative UI), and runs a Plan→Execute→Reflect agent harness with semantic routing and
caching in front of it.

**Two surfaces, one brain:** the floating widget (bottom-left, every page) and the full-page
view at `/assistant` share the same thread via `useConciergeChat` +
`ConciergeConversation` — start a conversation in one, continue in the other. The widget's
expand icon jumps to the page, and the widget hides itself on `/assistant`.

> History: `/assistant` previously hosted a separate Claude-based "move-out assistant" with
> write tools (create listing, confirm reservation). It was retired in favor of this concierge
> — one provider, one thread, and deliberately **read-only**: agent-initiated writes return
> only as explicitly user-confirmed actions.

It is a **fully additive module**: delete `backend/concierge/` and the two mounts, and the
rest of ReNest is untouched. The marketplace's own AI features (room-scan detection,
moderation, matching, copywriting) run on the same Gemini key via the shared
`listings/ai_client.py` helpers — one provider across the whole app.

## Turn pipeline

```
ConciergeWidget ──POST /api/concierge/chat/──▶ views → engine.run_turn()
                                                   │
                                    router.classify(text)          ── semantic ROUTER
                                     │                │
                              route "chat"      route "task"
                                     │                │
                        semantic_cache.lookup()       │            ── semantic CACHE
                          hit → instant reply         │
                                     │                │
                          fast model (flash)   reasoning model (pro)
                          search_help only     PLAN → EXECUTE → REFLECT
                                     │                │  (tools + generative UI)
                                     └──── persist + meta ────┘
```

### Semantic router (`router.py`)
Nearest-exemplar classification over the same deterministic hashed-term embeddings RAG uses,
with high-precision regex overrides (greetings; "my …" data; live-availability phrasing). No
LLM call — routing is free, instant, and unit-tested. Chat-route turns run on
`CONCIERGE_MODEL_FAST` (default `gemini-flash-latest`, thinking budget 0) with only the
knowledge-base tool; task turns run on `CONCIERGE_MODEL_REASONING` (default
`gemini-pro-latest`) with the full tool belt. Low-confidence matches fall through to the task
harness — the safer failure mode. **Why:** greetings and how-to questions stop paying
reasoning-model latency/cost, and multi-step questions stop getting shallow one-shot answers.

### Plan → Execute → Reflect (`orchestrator.py`)
1. **Plan** (JSON): goal, ≤4 tool steps, and *checkable success criteria*.
2. **Execute**: plan injected into the system instruction; bounded tool loop (≤6 rounds)
   over read-only marketplace tools + UI tools; every call/result trimmed into an evidence log.
3. **Reflect** (fast model, JSON): a Reflexion-style critic grades the draft against the
   success criteria and the evidence. A failed verdict buys exactly **one** revision pass with
   the critique injected; then we ship. Reflection failures fail-open to the draft.

**Why:** planning stops tool-wandering; criteria + evidence give the critic something objective,
so ungrounded claims get caught *before the user sees them* — hallucination control as a
pipeline stage, not a prompt plea. The UI shows "planned N steps · self-checked/self-corrected".

### Generative UI (`ui_blocks.py` + `generativeUi.jsx`)
The model composes interfaces; the server supplies the truth. Four presentation tools —
`show_listing_cards`, `show_move_out_progress`, `suggest_followups`, and
`propose_saved_search` — append typed blocks to the turn's collector. Suggestion chips can
carry an in-app `path` (validated against a route whitelist server-side — off-whitelist paths
degrade to plain send-chips, so the model can never mint an arbitrary link).
`propose_saved_search` is the concierge's first **write flow, and it is user-confirmed by
construction**: the model only renders a proposal card; the alert is created when the user
taps Create, through the same `/saved-searches` endpoint the settings page uses. The agent
itself stays read-only and the state-integrity proof stays intact. **Every prop is hydrated server-side from the ORM at call time**: for
cards the model contributes only listing IDs it saw in tool results; titles/prices/zones/thumbs
come from the database, and dead or invented IDs are dropped (a hallucinated card is
structurally impossible). The widget maps `block.type` → React component via a registry;
unknown types render nothing, so backend and frontend can evolve independently. Blocks persist
in `message.meta`, so history re-renders them. **Why:** tappable cards and progress bars beat
markdown walls; suggestion chips cut typing on mobile.

### Semantic cache (`semantic_cache.py`)
Before calling the model, chat-route queries are embedded and compared against recent cached
answers (cosine ≥ 0.90, TTL 1h, capped pool, hit/miss stats). **Only the chat route is
cacheable** — it can touch nothing but the shared knowledge base, so entries are
user-independent by construction. Task answers embed per-user live state and are never cached.
Cache hits skip the model entirely and render with an "Instant answer" caption. **Why:** repeat
how-to questions (the bulk of support traffic) cost zero tokens and ~0 latency, without ever
risking stale or cross-user marketplace data.

## Memory
Last 12 messages verbatim; beyond 28 unfolded rows, older turns fold into
`ConciergeThread.summary` via a flash-model call (deterministic digest fallback). Folded rows
keep widget scrollback; context stays bounded.

## Resilience
Bounded retries (2, backoff) on 429/5xx/network via duck-typed error codes; circuit breaker
(3 failed turns → 120s open, short-circuits pre-API); degraded turns persist nothing; kill
switch (`CONCIERGE_ENABLED=0` or no key) returns a calm offline payload with HTTP 200; the
widget is a lazy chunk behind its own ErrorBoundary — a crash removes the widget, never the
shell. Plan/reflect phases fail-open so an outage in an *optimisation* never blocks an answer.

## Configuration

| Env var | Default | Effect |
|---|---|---|
| `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) | unset | Unset = concierge offline, app unaffected |
| `CONCIERGE_ENABLED` | `1` | `0` disables without removing the key |
| `CONCIERGE_MODEL_REASONING` | `gemini-pro-latest` | Plan/execute/revise model |
| `CONCIERGE_MODEL_FAST` | `gemini-flash-latest` | Chat route, reflection, summarisation |
| `AI_MODEL_EMBEDDING` | `gemini-embedding-001` | RAG + semantic-cache embeddings |

Throttle: `concierge` scope, 40/hour per user.

## Embeddings — remote-first, deterministic fallback

RAG and the semantic cache use **real Gemini embeddings** when the key is present and fall
back to the original hashed bag-of-terms vectors offline. Every stored vector is tagged with
its space (`gemini-v1` / `hashed-v1`) and **cross-space vectors are never compared**; a
failing embedding API triggers a 5-minute cooldown instead of per-request timeouts. The
knowledge base (`concierge/knowledge/renest_help.md`) auto-seeds, auto-*upgrades* to the
remote space when a key appears, and auto-reseeds when the markdown changes on disk. The
router deliberately stays on hashed vectors + regex overrides: routing needs microsecond
latency, and "think harder" always forces the deep harness (the user's escape hatch).

## Streaming

`POST /api/concierge/chat/stream/` runs the same turn but emits SSE progress —
`planning → checking live listings → double-checking` — then the final payload. The widget
prefers it and falls back to the JSON endpoint on any hiccup, so streaming is purely
progressive enhancement. Server side: worker thread + queue feeding a `StreamingHttpResponse`.

## Quality loop

* **Feedback** — thumbs on every persisted reply (`POST /messages/<id>/feedback/`,
  up/down/clear, own-thread only) stored on `ConciergeMessage.rating`.
* **Observability** — staff-only `GET /api/concierge/stats/`: route mix, cache hit counts,
  reflection pass/fail, revision rate, latency avg/p95, ratings, breaker state, embedding
  space. Every turn stamps `meta.latency_ms`.
* **Evals** — `python manage.py run_concierge_evals` runs the golden set
  (`concierge/evals/golden.json`): router classification + KB retrieval offline (also
  enforced by the test suite, so prompt/KB/router edits are regression-gated); `--live` adds
  real-model turns graded by containment rules (needs a key, costs tokens). The harness paid
  for itself on day one: its first run caught a greeting-regex gap and a KB phrasing miss.
* **Injection containment** — the system prompt pins tool results as data-never-instructions,
  and a structural test proves user-generated content only ever reaches the model inside
  `function_response` payloads, never spliced into prompts.

## Tests

`backend/concierge/tests.py` (46): RAG, router, semantic cache (incl. TTL + never-cache-tasks),
per-tool read-only audits, UI hydration (hallucinated IDs cannot render), plan/reflect/revision
flows, bounded loops, retry/breaker/kill-switch, memory folding, API contract — and
**ConciergeStateIntegrityTests**: a full Plan→Execute→Reflect conversation with generative UI
leaves every marketplace table byte-identical and issues zero SQL writes outside `concierge_*`
tables. `ConciergeWidget.test.jsx` (8): open/history, plan captions, generative cards, unknown
block tolerance, suggestion chips, cached caption, error survival, offline, reset.
