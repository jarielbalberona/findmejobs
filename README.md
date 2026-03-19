# FindMeJobs

A single-host job intelligence pipeline that ingests postings from RSS feeds, ATS boards, and direct career pages, normalizes them into a canonical schema, deduplicates, ranks against your profile, stages sanitized review packets for an external reviewer (OpenClaw), and sends a deterministic digest of reviewed jobs.

Built with Python 3.12, SQLite (WAL mode), SQLAlchemy, Pydantic, and Typer.

## How it works

Source classification is explicit:
- Tier A predictable ATS adapters
  - Ashby
  - Greenhouse
  - Lever
  - SmartRecruiters
- Tier B PH board adapters
  - JobStreet Philippines
  - Kalibrr
  - Bossjob
  - foundit Philippines
- Tier C generic fallback
  - direct-page parser

```
sources (RSS discovery, Tier A ATS, Tier B PH boards, Tier C direct pages)
        │
        ▼
    ┌────────┐    raw payloads    ┌───────────┐    canonical    ┌────────┐
    │ Ingest │ ─────────────────▶ │ Normalize │ ─────────────▶ │ Dedupe │
    └────────┘    saved to disk   └───────────┘     jobs       └────────┘
                                                                    │
        ┌───────────────────────────────────────────────────────────┴─────────┐
        ▼                                                                     ▼
    ┌──────┐    scored clusters    ┌────────┐    sanitized JSON    ┌──────────┐
    │ Rank │ ────────────────────▶ │ Review │ ──────────────────▶ │ OpenClaw │
    └──────┘                       └────────┘    (outbox/inbox)   └──────────┘
        │
        ▼
    ┌────────┐
    │ Digest │
    └────────┘
```

Raw source payloads are stored under `var/raw/` before normalization. The external reviewer (OpenClaw) never reads raw artifacts or SQLite directly; it only sees sanitized JSON packets staged in the outbox and writes structured results back into the inbox.

Slice 2.5 added application drafting downstream of ranking and review eligibility. Drafting uses bounded application packets built from canonical job fields, score summaries, sanitized review excerpts, and canonical profile data. No auto-submit, browser automation, or raw hostile page content is allowed in this stage.

## Quick start

**Requirements:** Python 3.12+

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Configure sources

Copy and edit the example configs under `config/`:

- `config/app.toml` — database, storage paths, HTTP settings
- `config/profile.toml` — legacy single-file job preferences for ranking
- `config/profile.yaml` + `config/ranking.yaml` — canonical profile bootstrap outputs, also accepted by the runtime
- `config/sources.d/*.toml` — one file per source

Enable at least one source and point it at a real endpoint:

```toml
# config/sources.d/my-feed.toml
name = "my-feed"
kind = "rss"
enabled = true
feed_url = "https://example.com/jobs.rss"
```

```toml
# config/sources.d/my-board.toml
name = "my-board"
kind = "greenhouse"
enabled = true
board_token = "your-company"
include_content = true
```

Other supported source kinds (added in Slice 2):
- `lever`
- `ashby`
- `smartrecruiters`
- `jobstreet_ph`
- `kalibrr`
- `bossjob_ph`
- `foundit_ph`
- `direct_page`

Operational rules by tier:
- Tier A predictable ATS adapters are the cleanest public structured sources and should carry the highest default trust.
- Tier B PH board adapters are usable but more fragile. Treat them as board-specific parsers with lower default trust and stronger parser-health scrutiny.
- Tier C direct-page parsing is the fallback, not the gold standard. Use it conservatively and expect weaker extraction quality than Tier A.

For ATS sources, set `company_name` explicitly when the public payload does not provide a trustworthy employer name. The local source `name` is an operator identifier, not canonical company identity.
Do not pretend all sources are equally clean. Ranking trust, observability expectations, and operational triage should follow the tier model above.
For Tier B PH board sources, watch `report` for `raw_seen`, `seen`, `skipped`, and `skip_ratio`. A “successful” run with a rising skip ratio is still a degraded parser.

Shipped example source configs:
- `config/sources.d/example-ashby.toml`
- `config/sources.d/example-bossjob-ph.toml`
- `config/sources.d/example-direct-page.toml`
- `config/sources.d/example-foundit-ph.toml`
- `config/sources.d/example-greenhouse.toml`
- `config/sources.d/example-jobstreet-ph.toml`
- `config/sources.d/example-kalibrr.toml`
- `config/sources.d/example-lever.toml`
- `config/sources.d/example-rss.toml`
- `config/sources.d/example-smartrecruiters.toml`

### Run the pipeline

```bash
alembic upgrade head          # apply migrations
findmejobs ingest             # fetch and normalize postings
findmejobs rank               # score against your profile
findmejobs review export      # stage sanitized packets for review
findmejobs digest send        # send deterministic digest for eligible reviewed jobs
findmejobs report             # source/pipeline/delivery summary
findmejobs doctor             # verify runtime health
```

After OpenClaw writes results into `var/review/inbox/`:

```bash
findmejobs review import-results
```

## Application drafting (Slice 2.5)

Application drafting prepares reviewable application artifacts from ranked, eligible jobs. The app remains the system of record. OpenClaw only ever receives bounded request packets written under the application job directory.

Typical flow:

```bash
findmejobs prepare-application --job-id <normalized_job_id>
findmejobs draft-cover-letter --job-id <normalized_job_id>
findmejobs draft-answers --job-id <normalized_job_id> --questions-file questions.yaml
findmejobs show-application --job-id <normalized_job_id>
findmejobs validate-application --job-id <normalized_job_id>
findmejobs regenerate-application --job-id <normalized_job_id>
```

`prepare-application` validates that the job exists, is normalized, passed current ranking thresholds, and belongs to the active profile/rank model pair. It then writes a bounded application packet and OpenClaw drafting requests.

Generated artifacts:

```text
state/applications/<job_id>/application_packet.json
state/applications/<job_id>/cover_letter.draft.md
state/applications/<job_id>/cover_letter.meta.json
state/applications/<job_id>/answers.draft.yaml
state/applications/<job_id>/answers.meta.json
state/applications/<job_id>/missing_inputs.yaml
state/applications/<job_id>/draft_report.md
state/applications/<job_id>/history/<timestamp>/*
state/applications/<job_id>/openclaw/cover_letter.request.json
state/applications/<job_id>/openclaw/cover_letter.result.json
state/applications/<job_id>/openclaw/answers.request.json
state/applications/<job_id>/openclaw/answers.result.json
```

Application packet contents:
- canonical job summary and links
- source metadata and trust/priority
- score total plus score breakdown summary
- sanitized review-packet excerpt and matched signals
- matched profile summary
- relevant strengths
- detected gaps and unknowns
- explicit application questions from source payload or operator-provided file
- safe drafting context only

Drafting rules:
- drafts only, never submission
- no raw HTML, full page dumps, or arbitrary scraped text in the drafting boundary
- no invented claims, fake enthusiasm, or guessed personal details
- salary, notice period, availability, relocation, work authorization, and time-zone commitments stay flagged as missing unless present in canonical profile data
- local template drafts are written immediately; OpenClaw result files can replace them later only after bounded packet validation and missing-input checks pass
- `validate-application` is strict: missing drafts, stale drafts, invalid draft metadata, or invalid imported outputs fail validation instead of reporting a misleading success
- repeated prepare/draft runs snapshot the previous artifacts into `history/` before overwriting current files

Optional profile fields for application drafting can live under `[application]` in `config/profile.toml`:

```toml
[application]
professional_summary = "Backend engineering work focused on Python, SQL, and reliable delivery."
key_achievements = ["Improved backend reliability for production APIs."]
project_highlights = ["Built backend services and APIs with Python and SQL in production environments."]
salary_expectation = "Open to discussing a market-aligned package for the role and location."
notice_period = "Two weeks."
current_availability = "Available after a standard notice period."
remote_preference = "Remote-first."
relocation_preference = "Open to discussion."
work_authorization = "Please confirm based on target country requirements."
work_hours = "Able to support overlap with Asia/Manila business hours."
```

## Profile onboarding

`findmejobs` includes a local-first profile bootstrap flow for importing a resume and generating draft profile config with OpenClaw assistance.

Supported inputs:
- PDF
- DOCX
- TXT
- Markdown
- JSON Resume
- pasted plain text

Typical flow:

```bash
findmejobs profile import --file resume.pdf
findmejobs profile import                         # refresh the current pending import after OpenClaw writes a result
findmejobs profile show-draft
findmejobs profile missing
findmejobs profile validate-draft
findmejobs profile diff
findmejobs profile promote-draft
```

Files written by the bootstrap flow:

```text
state/profile_bootstrap/input/<original file>
state/profile_bootstrap/extracted/resume.txt
state/profile_bootstrap/extracted/resume.meta.json
state/profile_bootstrap/drafts/profile.draft.yaml
state/profile_bootstrap/drafts/ranking.draft.yaml
state/profile_bootstrap/drafts/missing_fields.yaml
state/profile_bootstrap/drafts/import_report.md
state/profile_bootstrap/drafts/raw_draft_response.json
config/profile.yaml
config/ranking.yaml
```

Behavior rules:
- extracted text is persisted before any OpenClaw-assisted draft generation
- deterministic baseline extraction populates obvious resume facts before any OpenClaw refinement
- raw draft-generation output is saved before parsing into final YAML and Markdown artifacts
- resume import is bootstrap input only, not final truth
- missing and low-confidence fields are surfaced in draft artifacts
- hard preferences such as salary floor, remote-only requirement, blocked companies, and blocked titles are left unset unless explicitly stated
- weak or practically empty drafts fail validation instead of being written as misleading placeholders
- promotion validates the draft and fails clearly if incomplete
- reimport comparison does not silently overwrite explicit canonical preferences
- runtime commands can load the promoted `config/profile.yaml` plus sibling `config/ranking.yaml`

Optional refinement:

```bash
findmejobs profile import --file resume.pdf
# OpenClaw writes state/profile_bootstrap/review/openclaw_result.json
findmejobs profile import --answers-text "Remote is preferred. No relocation."
```

You can also pass `--answers-file <path>` to `profile import` or `profile reimport`. Follow-up answers are staged as a second sanitized OpenClaw refinement request and merged conservatively into the draft.

Example drafts:
- `config/examples/profile.draft.yaml`
- `config/examples/ranking.draft.yaml`

### Run tests

```bash
pytest
```

## Project structure

```
src/findmejobs/
  application/         Application packet generation and draft storage
  cli/                 Typer CLI commands
  config/              Typed config models and TOML loaders
  db/                  SQLAlchemy models, session factory, repositories
  domain/              Canonical domain types (job, ranking, review, source)
  ingestion/           HTTP fetcher and source adapters (RSS, Greenhouse, Lever, Ashby, SmartRecruiters, direct pages)
  normalization/       Raw-to-canonical transformation rules
  dedupe/              Exact layered deduplication and clustering
  ranking/             Hard filters and weighted scoring signals
  review/              Sanitized packet builder, export/import, OpenClaw client
  delivery/            Deterministic digest building and email delivery
  feedback.py          Explicit operator feedback recording
  observability/       Structured logging and doctor health checks
  utils/               URL canonicalization, hashing, IDs, locking, text helpers

config/
  app.toml             Runtime configuration
  profile.toml         Legacy runtime profile
  profile.yaml         Canonical bootstrap profile
  ranking.yaml         Canonical bootstrap ranking policy
  sources.d/           One TOML file per source

systemd/               Example service and timer units for scheduled runs
```

## Deployment

Designed to run on a single Linux host with systemd timers.

```bash
sudo adduser --system --group --home /opt/findmejobs findmejobs
sudo mkdir -p /opt/findmejobs /etc/findmejobs/sources.d /var/lib/findmejobs
sudo chown -R findmejobs:findmejobs /opt/findmejobs /var/lib/findmejobs
```

Install the app into `/opt/findmejobs`, then copy configs:

```
config/app.toml           → /etc/findmejobs/app.toml
config/profile.toml       → /etc/findmejobs/profile.toml
config/sources.d/*.toml   → /etc/findmejobs/sources.d/
```

Update `/etc/findmejobs/app.toml` for production paths:

```toml
[database]
url = "sqlite:////var/lib/findmejobs/app.db"

[storage]
root_dir       = "/var/lib/findmejobs"
raw_dir        = "/var/lib/findmejobs/raw"
review_outbox_dir = "/var/lib/findmejobs/review/outbox"
review_inbox_dir  = "/var/lib/findmejobs/review/inbox"
lock_dir       = "/var/lib/findmejobs/locks"

[delivery]
channel = "email"
daily_hour = 8
digest_max_items = 10

[delivery.email]
enabled = true
host = "smtp.example.com"
port = 587
username = "smtp-user"
password = "smtp-password"
use_tls = true
sender = "findmejobs@example.com"
recipient = "you@example.com"
```

Enable the timers:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now findmejobs-ingest.timer
sudo systemctl enable --now findmejobs-rank.timer
sudo systemctl enable --now findmejobs-review-export.timer
sudo systemctl enable --now findmejobs-review-import.timer
sudo systemctl enable --now findmejobs-digest.timer
sudo systemctl enable --now findmejobs-doctor.timer
```

Operational notes:

- `ingest` exits non-zero if any enabled source fails. systemd will surface partial source failure instead of hiding it.
- `ingest`, `rank`, `review export`, and `review import` all serialize on one shared pipeline lock at `/var/lib/findmejobs/locks/pipeline.lock`. Separate timers will not run those write stages concurrently.
- `digest send` is idempotent for a digest date unless you explicitly use `digest resend`.
- `report` summarizes source health, pipeline outcomes, ranking totals, and delivery outcomes from SQLite.

## OpenClaw review contract

OpenClaw reads packet JSON from the outbox and writes result JSON into the inbox:

```json
{
  "packet_id": "packet-id",
  "provider_review_id": "optional-provider-id",
  "decision": "keep",
  "confidence_label": "medium",
  "reasons": ["Strong title match", "Good Python fit"],
  "draft_summary": "Worth reviewing.",
  "draft_actions": ["Open posting", "Check compensation"],
  "reviewed_at": "2026-03-19T10:00:00Z",
  "raw_response": {
    "model": "openclaw",
    "notes": "optional"
  }
}
```

## Slice history

**Slice 1** — core pipeline: typed config, SQLite/WAL, Alembic migrations, RSS and Greenhouse ingestion, raw document storage, canonical normalization, exact layered dedupe, deterministic ranking with hard filters, sanitized review packets, OpenClaw file-boundary client, Typer CLI, structured logging, systemd timer examples.

**Slice 2** — expanded sources and operations: profile bootstrap from CV/resume, additional ATS adapters (Ashby, Lever, SmartRecruiters), PH board adapters (JobStreet Philippines, Kalibrr, Bossjob, foundit Philippines), direct-page extraction, deterministic ranking with title families/company preferences/timezone fit/source trust/stale decay, explicit operator feedback, digest email delivery, reporting, rerank/reprocess commands.

**Slice 2.5** — application drafting: bounded application packets from canonical job fields, score summaries, sanitized review excerpts, and canonical profile data. Drafts only, never submission.

## Slice 3 remains deferred

Still out of scope after Slice 2.5:
- auto-submit
- browser automation
- LinkedIn Easy Apply
- CAPTCHA solving
- board credentials or session storage
- multi-agent application workflows
- interview scheduling or downstream CRM/state-machine automation

## Known limitations

- Salary parsing is minimal
- Direct-page extraction is intentionally conservative and will not rescue hostile JS-heavy sites
- PH board adapters are listing-source parsers with lower default trust than predictable ATS sources
- Review export/import is filesystem-based, not API-based
- Email is the only delivery channel
- SQLite remains single-host only by design
- Fuzzy dedupe is not yet implemented

## License

Private.
