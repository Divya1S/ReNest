# Roadmap

Planned work, roughly in priority order. Shipped features are described in
the [README](../README.md) and the docs in this folder.

## Near term

- **Unified search pipeline** — merge full-text, semantic, and
  natural-language-parsed search into one ranked query path with a single
  relevance score, replacing the current mode-switching between the three.
- **Concierge token streaming** — stream the assistant's final answer
  word-by-word over the existing SSE channel (phase events already stream;
  the answer itself currently arrives in one chunk).
- **Production launch hardening** — first public deployment via the Render
  blueprint, real SMTP + object storage, Sentry wired to alerts, and a
  post-deploy smoke checklist run against the live host.

## Later

- **Reservation chat upgrades** — typing indicators and read receipts on the
  handoff chat, building on the existing poll-based message sync.
- **App Store / Play Store submission** — the Capacitor shell builds today
  (see [mobile.md](mobile.md)); remaining work is store metadata, review
  screenshots, and a TestFlight/internal-testing pass.
- **Partner API pilot** — exercise the embeddable widget and partner API
  with one real campus sustainability office and fold their feedback back
  into the docs.
- **Demand forecasting v2** — replace the linear-regression demand model
  with seasonal decomposition once two full move-out cycles of data exist.

## Non-goals (for now)

- Payments between students. The only money flow is the campus license and
  optional listing boosts; peer-to-peer payments would change the product's
  trust model and compliance surface.
- A separate native mobile codebase. The Capacitor shell wraps the same
  React app on purpose — one codebase, three platforms.
