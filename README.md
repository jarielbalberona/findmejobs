# FindMeJobs

A single-host job intelligence pipeline that ingests postings from RSS feeds and ATS boards, normalizes them into a canonical schema, deduplicates, ranks against your profile, and stages sanitized review packets for an external reviewer (OpenClaw).

Built with Python 3.12, SQLite (WAL mode), SQLAlchemy, Pydantic, and Typer.

## How it works

```
sources (RSS, Greenhouse)
        │
        ▼
    ┌────────┐    raw payloads    ┌───────────┐    canonical    ┌────────┐
    │ Ingest │ ─────────────────▶ │ Normalize │ ─────────────▶ │ Dedupe │
    └────────┘    saved to disk   └───────────┘     jobs       └────────┘
                                                                    │
        ┌───────────────────────────────────────────────────────────┘
        ▼
    ┌──────┐    scored clusters    ┌────────┐    sanitized JSON    ┌──────────┐
    │ Rank │ ────────────────────▶ │ Review │ ──────────────────▶ │ OpenClaw │
    └──────┘                       └────────┘    (outbox/inbox)   └──────────┘
```

Raw source payloads are stored under `var/raw/` before normalization. The external reviewer (OpenClaw) never reads raw artifacts or SQLite directly — it only sees sanitized JSON packets staged in the outbox and writes structured results back into the inbox.

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

### Run the pipeline

```bash
alembic upgrade head          # apply migrations
findmejobs ingest             # fetch and normalize postings
findmejobs rank               # score against your profile
findmejobs review export      # stage sanitized packets for review
findmejobs doctor             # verify runtime health
```

After OpenClaw writes results into `var/review/inbox/`:

```bash
findmejobs review import-results
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
config/profile.yaml
config/ranking.yaml
```

Behavior rules:
- extracted text is persisted before any OpenClaw-assisted draft generation
- resume import is bootstrap input only, not final truth
- missing and low-confidence fields are surfaced in draft artifacts
- hard preferences such as salary floor, remote-only requirement, blocked companies, and blocked titles are left unset unless explicitly stated
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
  cli/                 Typer CLI commands
  config/              Typed config models and TOML loaders
  db/                  SQLAlchemy models, session factory, repositories
  domain/              Canonical domain types (job, ranking, review, source)
  ingestion/           HTTP fetcher and source adapters (RSS, Greenhouse)
  normalization/       Raw-to-canonical transformation rules
  dedupe/              Exact layered deduplication and clustering
  ranking/             Hard filters and weighted scoring signals
  review/              Sanitized packet builder, export/import, OpenClaw client
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
```

Enable the timers:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now findmejobs-ingest.timer
sudo systemctl enable --now findmejobs-rank.timer
sudo systemctl enable --now findmejobs-review-export.timer
sudo systemctl enable --now findmejobs-review-import.timer
sudo systemctl enable --now findmejobs-doctor.timer
```

Operational notes:

- `ingest` exits non-zero if any enabled source fails. systemd will surface partial source failure instead of hiding it.
- `ingest`, `rank`, `review export`, and `review import` all serialize on one shared pipeline lock at `/var/lib/findmejobs/locks/pipeline.lock`. Separate timers will not run those write stages concurrently.

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

## Current scope and limitations

This is **Slice 1** — the smallest production-meaningful version of the pipeline.

**Included:** typed config, SQLite/WAL, Alembic migrations, RSS and Greenhouse ingestion, raw document storage, canonical normalization, exact layered dedupe, deterministic ranking with hard filters, sanitized review packets, OpenClaw file-boundary client, Typer CLI, structured logging, systemd timer examples.

**Not yet included:** Ashby/Workable adapters, generic direct-page parsing, fuzzy dedupe, digest delivery, browser automation, LinkedIn scraping, auto-submit.

**Known limitations:**
- Salary parsing is minimal
- Review export/import is filesystem-based, not API-based
- No notification or digest channel yet

## License

Private.
