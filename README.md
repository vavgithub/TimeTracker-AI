# TimeTracker-AI

AI-powered work intelligence pipeline for Value at Void.

## What it does

Automatically tracks every teammate's work activity, maps it to ClickUp tasks using Gemini AI, and generates daily productivity insights and EOD reports — all without any manual input.

- Reads activity chunks from Supabase (sent by the Electron desktop app every 2 minutes)
- Processes each teammate's data separately using Gemini 2.5 Flash
- Maps work sessions to ClickUp tasks with AI confidence scoring
- Detects meetings, overdue tasks, and estimate vs actual time
- Generates EOD summaries and posts them to ClickUp automatically
- Pushes processed insights to the admin portal every 30 minutes

## Architecture

```
Electron app (teammate's laptop)
    ↓ activity chunks every 2 min
Supabase
    ↓ read every 30 min
worker.py (Render cron job)
    ↓ per-user AI processing
Ankit's backend (Render)
    ↓ stored in
Supabase (DailySummary, EodReport, SkillProfile)
    ↓ served to
Admin portal → San sees team insights
```
## Stack

- Python 3.12
- Vertex AI (Gemini 2.5 Flash) — task mapping + EOD narrative
- Supabase — activity data source + insights storage
- ClickUp API — task fetching + EOD posting
- Google Calendar API — meeting detection
- Render — cron worker deployment

## Project structure
```
backend/
integrations/
aw/          — ActivityWatch client + session builder
clickup/     — ClickUp API client
gcal/        — Google Calendar integration
supabase/    — Supabase client + chunk adapter
pipeline/
mapping/     — AI task mapper + meeting mapper
metrics/     — Productivity, trends, weekly insights
eod/         — EOD summary writer + skill profile
storage/     — File writers + push to server
main.py        — Personal pipeline (reads local AW)
worker.py      — Multi-user cron worker (reads Supabase)
scripts/
eod_post.sh        — EOD post script
work_hours_aw.sh   — AW health check
```
## Environment variables

```
Copy `.env.example` to `.env` and fill in:
CLICKUP_TOKEN=
CLICKUP_TEAM_ID=
CLICKUP_USER_EMAIL=
GCP_PROJECT_ID=
GCP_REGION=us-central1
GOOGLE_APPLICATION_CREDENTIALS=
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
ADMIN_SERVER_URL=
PUSH_API_KEY=
EOD_CLICKUP_CHANNEL_ID=
```

## Deployment

Worker runs as a Render cron job:
- Schedule: `*/30 * * * *`
- Command: `python3 backend/worker.py`
- EOD posts automatically at 7pm IST

## Personal Trace dashboard

`main.py` runs locally on Khyathi's laptop via cron.
Reads from local Activi
