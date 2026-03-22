# FindMeJobs

`findmejobs` is a single-host Python job intelligence pipeline and system of record.

It fetches jobs from public sources, stores raw payloads, normalizes to one canonical model, deduplicates, ranks deterministically, and exports sanitized review packets for OpenClaw-assisted review.

## Non-negotiables

- OpenClaw is **not** the raw scraper for this system.
- Raw hostile content must never flow into OpenClaw prompts.
- Ranking is deterministic and explainable; no LLM-scored ranking.
- The pipeline is rerunnable and auditable (`ingest`, `rank`, `review`, `digest`, `rerank`, `reprocess`).

Detailed guardrails and adapter guidance live in `AGENTS.md` and `CONTRIBUTING.md`.

## What ships today

- Profile bootstrap (resume import -> draft -> promote)
- Source ingestion (RSS, ATS adapters, PH board adapters, conservative direct page parser)
- Raw payload capture before normalization
- Canonical normalization + layered dedupe
- Deterministic ranking + explain tooling
- Sanitized review packet export/import
- Deterministic digest send/resend
- Bounded application drafting (draft only, never submission)

## Explicitly out of scope

- LinkedIn/Easy Apply automation
- Browser automation as a default ingestion path
- Auto-submit applications
- CAPTCHA solving / credential vault workflows
- Multi-agent application automation
- Web dashboard as primary ops path
- Postgres unless SQLite is proven insufficient

## Setup

Requirements: Python 3.12+

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head
```

The default DB URL is `sqlite:///./var/app.db`. Run Alembic from the repo root.

## Initialize local runtime config

Runtime files are local and gitignored:

- `config/app.toml`
- `config/profile.yaml`
- `config/ranking.yaml`
- `config/sources.yaml`

Initialize and validate:

```bash
findmejobs config init
findmejobs config validate
```

Show resolved config:

```bash
findmejobs config show-effective --json
```

SMTP password (if email delivery is enabled) is env-only:

```bash
export FINDMEJOBS_SMTP_PASSWORD='...'
```

If email delivery is disabled, leave `delivery.email.enabled = false` and skip `digest` commands.

## Bootstrap profile first

Do this before ranking tweaks.

```bash
findmejobs profile import --file /path/to/resume.pdf
findmejobs profile show-draft
findmejobs profile missing
findmejobs profile validate-draft
findmejobs profile diff
findmejobs profile promote-draft
```

Supported import inputs:

- PDF
- DOCX
- TXT
- Markdown
- JSON Resume
- pasted text via `findmejobs profile import --text "..."`

Bootstrap artifacts live under `state/profile_bootstrap/`. Canonical files after promote are `config/profile.yaml` and `config/ranking.yaml`.

## Configure sources

Minimal `config/sources.yaml` example:

```yaml
version: v1
sources:
  - name: my-feed
    kind: rss
    enabled: true
    feed_url: https://example.com/jobs.rss
```

Supported source kinds:

- `rss`
- `greenhouse`
- `lever`
- `ashby`
- `smartrecruiters`
- `workable`
- `jobstreet_ph`
- `kalibrr`
- `bossjob_ph`
- `foundit_ph`
- `direct_page`

Manage sources via CLI (not manual YAML surgery):

```bash
findmejobs sources list
findmejobs sources add --json '{"name":"my-feed","kind":"rss","feed_url":"https://example.com/jobs.rss"}'
findmejobs sources set my-feed --enabled --trust-weight 1.0
findmejobs sources disable my-feed
findmejobs sources remove my-feed --yes
```

Source trust is intentionally tiered:

- Tier A ATS adapters: higher default trust
- Tier B PH board adapters: more brittle, test harder
- Tier C direct-page parser: fallback only

## Daily operator flow

```bash
findmejobs doctor
findmejobs ingest
findmejobs rank
findmejobs jobs list --limit 100
findmejobs review export
findmejobs review import
findmejobs report
```

Notes:

- `findmejobs rank` runs `scripts/export_ui_data.sh` by default; disable with `--no-export-ui-data`.
- `findmejobs doctor` may report onboarding-state warnings (for example no successful ingest yet) before first successful pipeline run.

Useful variants:

```bash
findmejobs ingest --source greenhouse
findmejobs jobs list --all-scored --limit 100
findmejobs jobs list --json | jq '.jobs[:5]'
findmejobs ranking explain
findmejobs ranking set --minimum-score 40 --stale-days 45 --add-blocked-company "Bad Co"
findmejobs profile set --add-target-title "Senior Backend Engineer"
findmejobs digest send --dry-run
findmejobs digest resend --digest-date 2026-03-19 --dry-run
```

## Local UI (read-only)

The repo ships a static dashboard at `ui/` backed by snapshot files in `var/ui-data/`.

What you can view:

- Overview (pipeline summary and recent runs)
- Profile & Settings
- Ranking policy and weights
- Sources and source health
- Jobs (search, filters, score/status sorting)

Generate snapshot data:

```bash
./scripts/export_ui_data.sh
```

Serve the repo root and open the UI:

```bash
python3 -m http.server 4173
```

Open `http://127.0.0.1:4173/ui/`.

Refresh behavior:

- Click `Reload Data` in the UI to re-fetch current snapshot files.
- Re-run `./scripts/export_ui_data.sh` after pipeline changes.
- `findmejobs rank` already triggers UI export by default.

## Command surface

Top-level:

- `findmejobs doctor`
- `findmejobs ingest`
- `findmejobs rank` / `findmejobs rerank`
- `findmejobs report`
- `findmejobs prepare-application`
- `findmejobs draft-cover-letter`
- `findmejobs draft-answers`
- `findmejobs show-application`
- `findmejobs validate-application`
- `findmejobs regenerate-application`

Groups:

- `findmejobs config ...`
- `findmejobs profile ...`
- `findmejobs ranking ...`
- `findmejobs jobs ...`
- `findmejobs review ...`
- `findmejobs digest ...`
- `findmejobs feedback ...`
- `findmejobs reprocess ...`
- `findmejobs sources ...`

Explore help:

```bash
findmejobs --help
findmejobs <group> --help
findmejobs <group> <command> --help
```

## Ranking model behavior

- Hard filters run before scoring and emit reason codes per job score row.
- Soft scoring uses weighted signals from `config/ranking.yaml`.
- Rules are always evaluated; you change behavior through config values/lists/weights, not by hidden toggles.
- Use `findmejobs ranking explain` to inspect reason-code and signal-to-config mapping.

## Trust boundary

Keep this boundary intact:

- Raw payloads are captured before normalization.
- OpenClaw does not scrape job pages for this system.
- OpenClaw only sees sanitized review/application inputs.
- Review logic stays separate from ingestion/parsing logic.

## Application drafting (bounded)

Draft flow:

```bash
findmejobs prepare-application --job-id <job_id>
findmejobs draft-cover-letter --job-id <job_id>
findmejobs draft-answers --job-id <job_id>
findmejobs validate-application --job-id <job_id>
```

Artifacts are stored in `state/applications/<job_id>/`. This flow creates drafts only; it never submits applications.

## Deployment model

Designed for one Linux host with systemd timers.

Typical prod layout:

- config: `/etc/findmejobs/`
- state: `/var/lib/findmejobs/`

Enable timers after install/config:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now findmejobs-ingest.timer
sudo systemctl enable --now findmejobs-rank.timer
sudo systemctl enable --now findmejobs-review-export.timer
sudo systemctl enable --now findmejobs-review-import.timer
sudo systemctl enable --now findmejobs-doctor.timer
```

If email delivery is enabled, also enable `findmejobs-digest.timer`.

## Tests

```bash
pytest
```

## Known limitations

- Salary parsing is still minimal.
- Direct-page parsing remains intentionally conservative.
- PH board adapters are more fragile than predictable ATS adapters.
