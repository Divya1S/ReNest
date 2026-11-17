# How ReNest is built

ReNest has three main parts:

- **A website** that runs in the browser. It is what people see and click.
- **A server** that stores the data, checks who is allowed to do what, and sends email and notifications.
- **A database** that keeps everything: accounts, listings, reservations and messages.

Everything else is optional and can be added later without changing the code.

## The parts

![Diagram: the website in the browser talks to the API on the server. The server reads and writes a PostgreSQL database, an optional Redis cache and optional photo storage. The assistant and the background jobs can call Google Gemini, an optional outside AI service.](diagrams/architecture.svg)

| Part | What it does | Built with |
|---|---|---|
| Website | Shows the pages, sends requests to the server | React, Vite, Tailwind CSS |
| API | Answers every request and enforces the rules, such as "only the owner can edit a listing" | Django, Django REST Framework |
| Database | Stores all the data | PostgreSQL (SQLite works for local use) |
| Background jobs | Send email and run scheduled work such as expiring old listings | Celery, or a scheduled request when there are no workers |
| Cache | Keeps short-lived data such as rate-limit counters | Redis, or the server's own memory when Redis is not set up |
| Photo storage | Holds uploaded photos | The server's disk, or S3-compatible storage such as Cloudflare R2 |
| AI service | Powers the assistant and the AI helpers | Google Gemini |

In production the server also delivers the built website, so there is only one thing to host.

## How a request travels

Take "reserve an item" as an example:

1. The website sends the request to the API, with the session cookie that proves who is signed in.
2. The API checks the rules: the item is still available, it is on your campus, and it is not your own.
3. The API writes the reservation to the database and marks the item as held.
4. The owner gets a notification. Notifications reach the browser over a connection that the server keeps open, so they appear without a page reload.

Steps 2 and 3 happen while the item's database row is locked. If two people reserve the same item at the same moment, exactly one of them gets it.

## The assistant

The assistant answers questions about ReNest and can look things up for you. It can only read. It never reserves, posts or changes anything.

Each message goes through these steps:

1. **Sort the message.** Simple rules, with no AI call, decide whether it is a general question ("how do handoffs work?") or a task ("what free lamps are near me?").
2. **General questions** are answered from the built-in help guide. A question that was asked before can be answered from a store of earlier answers. Only short questions in plain words are kept there, and only when the answer used nothing but the help guide.
3. **Tasks** take three steps: the assistant plans what to look up, looks it up, then checks its answer against what it found. Its lookups see exactly the listings the Browse page would show you, and nothing else.

The AI service is an outside dependency, so the assistant is built to keep working when it misbehaves:

- Each reply has a time limit, and failed calls are retried only a few times.
- After repeated failures the assistant stops calling the service for a while.
- When the daily allowance for the stronger AI model runs out, it switches to the lighter one.
- If nothing works, it answers with the matching section of the help guide and says so.

A fixed set of sample questions is checked automatically on every change. Those checks do not call the AI service, so they cost nothing and give the same result every time.

## Folder layout

```
backend/
  accounts/       sign-up, sign-in, campuses, profiles
  listings/       the marketplace: listings, reservations, handoffs, room scans
  hubs/           donation hubs and the tools for the people who run them
  concierge/      the assistant
  risk/           the Risk Intelligence demo
  dormcycle/      project settings and shared plumbing
frontend/
  src/            the website (pages, components, shared code)
  e2e/            browser tests
scripts/          build, load-test and backup scripts
docs/             this documentation and the screenshots
```

> The settings folder is called `dormcycle`, which was the project's first working title. The product is ReNest. The folder name was kept because renaming it touches every deployment file for no benefit to users.
