# ReNest

[![CI](https://github.com/Divya1S/ReNest/actions/workflows/ci.yml/badge.svg)](https://github.com/Divya1S/ReNest/actions/workflows/ci.yml)

ReNest is a marketplace for campus move-out. Students list the things they are leaving behind, and other students on the same campus pick them up for free or for a few dollars.

![The ReNest marketplace on a laptop screen, with a pickup chat on a phone beside it](docs/screenshots/hero.jpg)

## The problem

At the end of every semester, dorms fill their dumpsters with lamps, storage bins, fans and unopened supplies. Most of it still works. A few months later, new students buy the same things again.

The items are not the hard part. Time is. In the last days before move-out nobody wants to photograph, describe and price twenty things one at a time, or sit around waiting for a stranger who never shows up.

ReNest is built around that week. You take a photo of your room, tag what is worth saving, and post it all at once. Pickups are arranged in the app and confirmed with a PIN, so both people know the handoff happened.

## What you can do

**If you are moving out**

- Photograph your room and tag items on the photo to create listings in bulk
- Sort each item into sell, donate, keep or toss on one board
- Publish everything that is ready with one click
- Send items to a campus donation hub instead of listing them
- Follow a move-out checklist with tasks ordered by your deadline

**If you are looking for something**

- Browse and search listings without an account
- Reserve an item, agree a pickup time and chat with the owner
- Save listings, and save a search to get a push notification when something new matches it
- Post a request for something you need, so owners can offer it to you

**Around both**

- A six-digit PIN at pickup marks the handoff as complete
- Feedback after each handoff builds a trust profile for both people
- Listings can be reported, and staff have a moderation queue
- Each campus sees its own listings, with a leaderboard and impact numbers
- The website can be installed on a phone like an app

**With an AI key (optional)**

- Ask the assistant to find items, check your move-out progress or explain how ReNest works
- Let AI find the items in a room photo and start a draft listing for each one
- Type a search in plain words and have it turned into filters
- Have a listing description written from the title, category and condition

Some parts are optional and switch on with configuration: the AI features above (need an OpenAI key or a free Google Gemini key), email (needs an SMTP server) and push notifications (need a key pair). There is also a separate risk-analysis demo for sandbox payments, described in [docs/risk-intelligence.md](docs/risk-intelligence.md).

## Screenshots

All screenshots are from the application running locally with the demo data. The image at the top of this page is two of them placed side by side.

### Finding and reserving an item

| | |
|---|---|
| ![Marketplace grid of listings with search and category filters](docs/screenshots/browse.jpg) | ![A single listing with photo, price, owner trust card and a reserve button](docs/screenshots/listing-detail.jpg) |
| **Browse.** Search, filter by category or price, and see how long each item is still available. | **Listing.** Photo, condition, the owner's track record, and a form to request a pickup time. |

### Clearing out a room

| | |
|---|---|
| ![A room photo with tagged items and a form for the selected item](docs/screenshots/scan-studio.jpg) | ![Board with columns for review, sell, donate, keep, toss and done](docs/screenshots/clearout-board.png) |
| **Scan Studio.** Draw a box around each item on your room photo and fill in the details once. | **Clear-Out Board.** Every tagged item, grouped by what you decided to do with it. |
| ![Publish queue showing which drafts are ready and which need more details](docs/screenshots/publish-queue.png) | ![Move-out plan with a task checklist and progress](docs/screenshots/move-out-plan.jpg) |
| **Publish queue.** Shows which drafts are ready and what the others are missing, then publishes in bulk. | **Move-out plan.** A checklist of move-out tasks, each timed against your deadline. |

### Handing an item over

| | |
|---|---|
| ![Handoff page with pickup chat and a six-digit PIN](docs/screenshots/handoff-hub.png) | ![Dashboard with drafts ready to publish, pending pickups and savings](docs/screenshots/dashboard.png) |
| **Handoff Hub.** Chat, pickup times, and the PIN the person collecting shows at pickup. | **Dashboard.** What needs your attention today: drafts, pickups, alerts and feedback. |

### Community

| | |
|---|---|
| ![Request board where students post items they need](docs/screenshots/requests.png) | ![Directory of campus donation hubs](docs/screenshots/hubs.png) |
| **Requests.** Ask for something you need. Owners can offer one of their listings. | **Donation hubs.** Drop-off points for items you would rather give away. |

### On a phone

<p>
  <img src="docs/screenshots/mobile-browse.jpg" alt="Marketplace on a phone screen" width="250">
  &nbsp;&nbsp;
  <img src="docs/screenshots/mobile-listing.jpg" alt="A listing with its photo on a phone screen" width="250">
  &nbsp;&nbsp;
  <img src="docs/screenshots/mobile-handoff.png" alt="Pickup details and chat on a phone screen" width="250">
</p>

### AI features

These switch on when the server has an AI key (see [Settings](#settings)). The screenshots below were taken with an OpenAI key, and every answer in them came from the AI service while the app was running. Without a key the rest of ReNest works the same.

| | |
|---|---|
| ![Assistant answering two questions, with a listing card for a mini fan and a move-out progress card](docs/screenshots/ai-assistant.png) | ![A room photo with six items outlined by AI Detect and a draft listing for each](docs/screenshots/ai-room-scan.jpg) |
| **Assistant.** Two questions in a row. It searched the live listings and showed the match as a card, then read the move-out progress and suggested what to do next. It only looks things up. It never reserves or posts for you. | **Room scan.** One click on AI Detect. The AI found six items in this photo, drew a box around each one and started a draft listing for each with a title, category, condition and price type. |
| ![Marketplace search showing the filters Category: Storage and Free that the AI picked from a sentence](docs/screenshots/ai-search.jpg) | ![New listing form with a description written by AI from the title](docs/screenshots/ai-description.png) |
| **Search in plain words.** The search typed here was "free storage for my dorm room". The AI turned it into a search word and two filters, Storage and Free. | **Listing descriptions.** "Write with AI" drafts the description from the title, category and condition. You can edit it before posting. |

### Risk Intelligence

| | |
|---|---|
| ![Risk overview with transaction counts, decisions over time and risk distribution](docs/screenshots/risk-overview.png) | ![Risk Investigator answering a question, with facts from the data, a possible interpretation and the transactions used as evidence](docs/screenshots/ai-investigator.png) |
| **Overview.** A separate demo: sandbox payments scored by a rule engine. No real money is involved. | **Investigator.** A question in plain language. The numbers come from fixed database queries. Here the AI wrote the summary paragraph (this needs an AI key and `RISK_INVESTIGATOR_MODE=auto`). The summary is thrown away if it states a number that is not in the data. |

## How it works

1. **Sign up.** Create an account with your email. If your email domain belongs to a campus, you are placed in that campus.
2. **Post.** Add one listing by hand, or start a room scan: upload photos, tag items, decide what to do with each, and publish the ready ones together.
3. **Reserve.** Another student finds your item and requests it with a pickup time that suits them. The item is held for them while you decide.
4. **Confirm.** You approve the request. The app creates a pickup PIN, and the two of you can chat and settle the time.
5. **Hand over.** At pickup the other person shows you a six-digit PIN. You type it in, and the handoff is marked complete.
6. **Close the loop.** Both of you can leave feedback. Completed handoffs count toward your trust profile and the campus impact numbers.

## Built with

| Part | What it uses |
|---|---|
| Website | React, Vite and Tailwind CSS |
| Server | Python with Django and Django REST Framework |
| Database | PostgreSQL. SQLite works for a quick local start |
| AI features (optional) | OpenAI or Google Gemini |
| Tests | Django's test runner, Vitest and Playwright |

More detail is in [docs/architecture.md](docs/architecture.md).

## Run it locally

You need Python 3.13 and Node.js 22. PostgreSQL is optional: the steps below use SQLite, so there is nothing else to install.

**1. Start the server**

```bash
cd backend
cp .env.example .env
python -m venv ../.venv
../.venv/bin/pip install -r requirements-dev.txt
USE_SQLITE=1 ../.venv/bin/python manage.py migrate
USE_SQLITE=1 ../.venv/bin/python manage.py seed_demo
USE_SQLITE=1 ../.venv/bin/python manage.py runserver 127.0.0.1:8000
```

**2. Start the website** (in a second terminal)

```bash
cd frontend
npm install
npm run dev
```

**3. Open** http://localhost:5173

### Demo accounts

`seed_demo` creates two students on the same campus, with listings, a tagged room scan, donation hubs and a pickup that is already confirmed.

| Email | Password | What they have |
|---|---|---|
| `maya@renest.local` | `demo-pass-123` | A room scan and listings. She is giving a lamp to Leo. |
| `leo@renest.local` | `demo-pass-123` | Listings of his own, and a confirmed pickup of Maya's lamp. |

To try a full handoff, sign in as Leo in one window and as Maya in a private window. Leo's handoff page shows the PIN. Maya types it in on hers.

### Using PostgreSQL instead

```bash
docker compose up -d postgres
```

Then run the same server commands without `USE_SQLITE=1`. The connection settings in `.env.example` match the Docker container.

## Settings

Settings are read from `backend/.env`. The file `backend/.env.example` lists every setting with a short explanation. For local use you do not need to change anything.

The ones you are most likely to set:

| Setting | What it does |
|---|---|
| `OPENAI_API_KEY` | Turns on the assistant and the AI helpers using OpenAI. Keys come from [platform.openai.com](https://platform.openai.com/api-keys) and are billed by use. |
| `GEMINI_API_KEY` | The same features using Google Gemini instead. A free key is available at [aistudio.google.com](https://aistudio.google.com). Set one of the two. With neither, everything else still works. |
| `EMAIL_HOST` and the other `EMAIL_*` settings | Send real email. Without them, emails are printed in the server terminal. |
| `USE_SQLITE` | `1` uses a local SQLite file. `0` uses PostgreSQL. |

Before putting ReNest on the internet you must also set `DJANGO_DEBUG=0`, a secret key and your domain. The server refuses to start in that mode until they are set. See [docs/deployment.md](docs/deployment.md).

Never commit `backend/.env`. It is already listed in `.gitignore`.

## Run the tests

```bash
# Server
cd backend
../.venv/bin/python manage.py test
../.venv/bin/mypy .

# Website
cd frontend
npm test
npm run lint
npm run typecheck
npm run build
npm run e2e
```

There are about 970 server tests, 260 website unit tests and 34 browser tests. The browser tests use a mocked server, so they need nothing else running. The same checks run on GitHub for every pull request.

[CONTRIBUTING.md](CONTRIBUTING.md) has the full list of checks, including how to run the server tests against PostgreSQL.

## Limitations

- **It is a one-person project.** It is set up for a free hosting tier and has been load tested on a laptop only, not with real traffic.
- **The AI features depend on an outside service.** An OpenAI key is billed by use, and a free Gemini key has a small daily limit. When the service is slow or over its limit, the assistant falls back to answers from the help guide and the other AI helpers switch off.
- **Payments are a demo.** Risk Intelligence uses sandbox payments only. The optional Stripe features need your own Stripe account and are off by default.
- **Free hosting has trade-offs.** On the free tier the server sleeps when idle, uploaded photos need separate storage, and scheduled jobs are triggered by GitHub Actions. [docs/deployment.md](docs/deployment.md) explains each one.
- **There is no native mobile app.** The website works on phones and can be installed from the browser.

## More documentation

- [docs/architecture.md](docs/architecture.md): how the parts fit together, and the folder layout
- [docs/deployment.md](docs/deployment.md): hosting, scheduled jobs and backups
- [docs/risk-intelligence.md](docs/risk-intelligence.md): how sandbox payments are scored, how a repeated request is kept from charging twice, and the Fraud Lab
- [CONTRIBUTING.md](CONTRIBUTING.md): development setup and the checks every change must pass
- [SECURITY.md](SECURITY.md): how to report a vulnerability
