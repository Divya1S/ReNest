# Deploying ReNest

ReNest is set up to run for free on [Render](https://render.com). The file `render.yaml` describes everything Render needs, so there is very little to do by hand.

You get:

- one web service that runs both the server and the website
- one PostgreSQL database

## Deploy

1. Push this repository to your own GitHub account.
2. In the Render dashboard choose **New**, then **Blueprint**, and pick the repository.
3. Wait for the first build to finish. Render installs the dependencies, builds the website and sets up the database.

## After the first deploy

**1. Turn on the features you want.** Open the service's Environment page in Render and fill in the settings below. All of them are optional. A setting left empty only switches its own feature off.

| Setting | What it turns on |
|---|---|
| `GEMINI_API_KEY` | The assistant, item detection in room photos and AI-written descriptions. A free key is available at [aistudio.google.com](https://aistudio.google.com). |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL` | Real email: password resets and pickup reminders. Any email provider with SMTP works. |
| `AWS_S3_BUCKET_NAME` (and `AWS_S3_ENDPOINT_URL` for Cloudflare R2) | Photo storage that survives a redeploy. The bucket also needs a CORS rule that allows `PUT` from your site's address, because photos upload straight from the browser. |
| `PLATFORM_ADMIN_EMAIL` | The inbox that receives requests from new campuses. Defaults to `DEFAULT_FROM_EMAIL`. |
| `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY` | Push notifications. Create the pair with `npx web-push generate-vapid-keys`. |
| `SENTRY_DSN`, `VITE_SENTRY_DSN` | Error reporting. |

**2. Set up the scheduled jobs.** See [Scheduled jobs](#scheduled-jobs) below. Without them listings never expire, reminders are never sent, and saved-search alerts never fire.

**3. Create an admin account.** Open the Render Shell and run:

```bash
cd backend && python manage.py createsuperuser
```

The admin pages are at `/admin/`.

## Settings the server insists on

With `DJANGO_DEBUG=0` the server refuses to start until these are set. The Render blueprint fills them in for you. You only need to touch them when you attach your own domain.

| Setting | What it is |
|---|---|
| `DJANGO_SECRET_KEY` | A long random string. The placeholder values in this repository are rejected. |
| `DJANGO_ALLOWED_HOSTS` | The address of your site, for example `renest-web.onrender.com`. |
| `DJANGO_CSRF_TRUSTED_ORIGINS`, `CORS_ALLOWED_ORIGINS` | The same address with `https://` in front. Local addresses are rejected. |

The database comes from `DATABASE_URL`. `APP_BASE_URL` is the address used in emails and links. It defaults to the first trusted origin.

`backend/.env.example` lists every setting with a short explanation.

## Limits of the free tier

| Limit | What you notice | What to do about it |
|---|---|---|
| The free web service goes to sleep after 15 minutes without visitors | The first visit after a quiet period takes about a minute | The scheduled job contacts the site every 30 minutes, which wakes it up regularly. A visitor can still land on a slow first load |
| The free plan cannot run background workers | Email and AI work happens while the visitor waits | Nothing to do. Scheduled work is triggered from GitHub instead, see below |
| Render's free database is deleted 30 days after it is created | The site stops working | Use a database that stays free, such as Neon or Supabase. Set `DATABASE_URL` on the web service and delete the `databases:` section from `render.yaml` |
| Files on the server's disk are lost on every redeploy | Uploaded photos disappear | Set `AWS_S3_BUCKET_NAME`. Cloudflare R2 has a free tier |

To run real background workers later, remove the comment marks from the "Paid tier" section at the bottom of `render.yaml` and set `REDIS_URL`.

## Scheduled jobs

Some work has to happen on a timer, whether or not anybody is using the site. The jobs come in three groups:

| Group | How often | What it does |
|---|---|---|
| `tick` | Every 15 to 30 minutes | Expires old listings, sends pickup reminders, closes pickups that never happened, sends saved-search alerts, refreshes the trending list, delivers notifications held during quiet hours, and does the housekeeping for Risk Intelligence |
| `daily` | Once a day | Updates the demand forecast and removes expired sign-in sessions |
| `weekly` | Once a week | Recalculates each seller's completion rate and emails campus managers a summary |

One job failing does not stop the others. A group never runs twice at the same time: if a second run starts while the first is still going, its jobs are reported as skipped.

There are three ways to run them. Pick one.

**Option 1: GitHub Actions (the default, and free).** The workflow `.github/workflows/maintenance.yml` calls a password-protected address on your site on a schedule. Set it up once in your GitHub repository under **Settings**, **Secrets and variables**, **Actions**:

- a variable named `MAINTENANCE_URL` with your site's address, for example `https://renest-web.onrender.com`
- a secret named `MAINTENANCE_TOKEN` with the same value as the `MAINTENANCE_TOKEN` setting on the server. The blueprint creates one for you.

Until both are set, the workflow does nothing and does not report a failure. Once they are set, the workflow fails whenever a job fails, so a broken job shows up in red on GitHub.

GitHub switches scheduled workflows off on a public repository after 60 days without a commit. You can switch them back on from the Actions page.

**Option 2: Your own timer.** Any scheduler, or the Render Shell, can run the same jobs directly:

```bash
cd backend
python manage.py run_maintenance                 # every 15 to 30 minutes
python manage.py run_maintenance --jobs daily    # once a day
python manage.py run_maintenance --jobs weekly   # once a week
```

**Option 3: Celery beat.** If you run background workers with `REDIS_URL` set, Celery can do the scheduling. It runs the same three groups, so nothing is lost by switching. Use it instead of Option 1, not together with it.

## Backups

- `scripts/backup.sh` saves a copy of the database and checks the copy before it reports success.
- `scripts/restore.sh` restores a copy in a single step. If the restore fails, the database is left as it was.
- `.github/workflows/backup.yml` makes a backup every day and keeps it on GitHub for 7 days. To switch it on, add a `DATABASE_URL` secret to the repository (or the separate `POSTGRES_*` secrets). Until then it does nothing. If a backup comes out empty it fails, and nothing is uploaded.

**A warning about public repositories.** Files that a workflow saves on a public repository can be downloaded by anyone. Do not keep backups of real people's data there. Run the backup workflow from a private repository, or run `scripts/backup.sh` on a computer you control.
