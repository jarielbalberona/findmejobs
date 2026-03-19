# AGENTS.md

## Project: findmejobs

`findmejobs` is a standalone Python job intelligence pipeline.

It runs for one operator, one primary user profile, one Ubuntu LTS EC2 host, and local development on macOS.

The system is responsible for:

- profile bootstrap from CV/resume via OpenClaw assistance
- profile and ranking config persistence
- job ingestion from predictable ATS sources, PH job boards, and direct career pages
- raw document capture
- normalization into a canonical job model
- layered dedupe
- deterministic ranking
- sanitized review packet generation
- OpenClaw-assisted review and drafting from sanitized inputs only
- digest generation and delivery
- operator feedback
- rerank and reprocess workflows
- source health and pipeline observability

This repo is **not** an OpenClaw-native scraper.
This repo is the system of record.
OpenClaw is only an assistant around onboarding and review.

---

## Core rules

### 1. OpenClaw must never be the raw scraper
OpenClaw does **not** fetch raw job pages for this workflow.
All scraping, fetching, parsing, and normalization happen inside the Python app.

### 2. Raw hostile content must never flow directly into OpenClaw
Do not pass raw HTML, full scraped page dumps, arbitrary page instructions, or unbounded hostile content into review prompts.

OpenClaw may only see:

- sanitized review packets
- approved normalized fields
- profile bootstrap drafts
- operational summaries
- explicitly bounded extracted resume text during onboarding

### 3. Resume import is bootstrap input, not final truth
CV/resume import creates a **draft** profile and draft ranking config.
It does not write canonical config directly.

### 4. Ranking must remain deterministic
Ranking is rules-based and explainable.
Do not make ranking depend on LLM output.
Do not add hidden learning behavior.

### 5. Pipeline stages must be rerunnable and auditable
Ingest, normalize, dedupe, rank, review, digest, rerank, and reprocess must be safe to rerun.
Failures must be visible.
State changes must be explainable.

### 6. Keep the system boring
This is a modular monolith for one host.
Do not introduce fake enterprise complexity.
No microservices, Kafka, Celery, Redis, or browser automation unless explicitly approved.

---

## Available features

What the codebase is intended to deliver today (grouped for clarity; not a release checklist).

### Config and runtime
- Typed config loading (TOML and YAML)
- Runtime paths: `config/app.toml`, `config/profile.yaml`, `config/ranking.yaml`, `config/sources.d/*.toml` (operator-local; gitignored—templates under `config/examples/`)
- Typer CLI
- Structured logging, `doctor` health checks, operational `report`ing
- systemd service/timer examples for scheduled runs

### Data and persistence
- SQLite with WAL mode and Alembic migrations

### Ingestion and normalization
- RSS discovery and fetching
- Tier A ATS-style adapters: Greenhouse, Ashby, Lever, SmartRecruiters, Workable
- Tier B PH board adapters: JobStreet Philippines, Kalibrr, Bossjob, foundit Philippines
- Tier C generic direct-page parser (conservative)
- Raw payload capture on disk before normalization
- Canonical job normalization into one schema
- Layered exact dedupe and clustering (canonical URL, source key, normalized fields; fuzzy similarity remains deferred—see **What’s coming / deferred**)

### Profile bootstrap
- Resume import (PDF, DOCX, TXT, Markdown, JSON Resume, pasted text) with OpenClaw-assisted draft generation
- Draft profile and ranking YAML, missing-fields reporting, validate/diff/promote/reimport flows

### Ranking and feedback
- Deterministic ranking: hard filters, weighted signals, title families, company preferences, timezone fit, source trust, stale decay, operator feedback inputs
- `rank` / `rerank` and safe pipeline reruns

### Review and delivery
- Sanitized review packet export/import and strict OpenClaw file-boundary client
- Deterministic digest generation and email delivery (with resend where applicable)
- `reprocess` for review packets and targeted normalize reruns

### Application drafting (bounded)
- Application packet generation from canonical job data, scores, sanitized review excerpts, and profile fields
- OpenClaw drafting requests scoped under per-job application state
- Cover-letter and answers drafts only—never auto-submit

---

## What's coming / deferred

These are **not** part of the default product surface. Treat them as out of scope until explicitly approved. Operators see the same list under **What’s coming** in [README.md](README.md).

- LinkedIn scraping / Easy Apply–style flows
- Playwright or full browser automation for ingestion
- Auto-submit of applications
- CAPTCHA solving
- Board credentials or session storage
- Multi-agent application workflows
- Interview scheduling or downstream CRM / state-machine automation
- Postgres (only if SQLite proves insufficient)
- Web dashboard (only if single-host CLI ops are stable and it earns its complexity)
- Stronger fuzzy dedupe (if added, must stay bounded, tested, and auditable)

---

## Architecture

### System type
Modular monolith.

### Runtime model
Single-host CLI-driven pipeline with systemd services/timers on EC2.

### Trust zones

#### Untrusted ingestion zone
Handles:
- ATS/public board fetching
- PH board fetching
- Direct page fetching
- Raw payload storage
- Parsing
- Normalization

#### Trusted review zone
Handles:
- Sanitized review packets
- OpenClaw assessment/drafting
- Digest summaries
- Operator-facing outputs

Never blur these two zones.

---

## Source classification

Sources are not equal. Treat them differently.

### Tier A: predictable public ATS/job sources
Use official public job APIs/feeds where possible.

Examples: Ashby, Greenhouse, Lever, SmartRecruiters.

Higher default trust.

### Tier B: PH board adapters
Board-specific public page parsers, not clean ATS APIs unless proven otherwise.

Examples: JobStreet Philippines, Kalibrr, Bossjob, foundit Philippines.

Lower default trust. Stronger parser tests.

### Tier C: generic direct-page fallback
Used for public career pages when structured data or stable extraction is possible.

Never upgrade to "clean source" status without evidence.

---

## Repository expectations

### Module concerns
This repo should contain modules for:

- config loading and validation
- database and migrations
- profile bootstrap flow
- source adapters
- normalization
- dedupe
- ranking
- review packet generation
- OpenClaw integration boundary
- delivery
- CLI
- observability

### Configuration
- Committed templates: `config/examples/app.toml`, `config/examples/sources.d/*.toml`, and draft examples under `config/examples/`
- Operator-local (gitignored): `config/app.toml`, `config/profile.toml`, `config/profile.yaml`, `config/ranking.yaml`, `config/sources.d/*.toml`
- Runtime reads the **local** paths above after you copy from `config/examples/`

### State
- `state/profile_bootstrap/` — draft artifacts from resume import

Do not store canonical config inside draft/state folders.

---

## Profile bootstrap rules

### Draft flow
Draft artifacts may include:
- original imported resume
- extracted normalized resume text
- profile draft
- ranking draft
- missing fields report
- import report

### Missing preferences must not be guessed
Do not invent unsupported values for:
- salary floor
- remote-only requirement
- relocation preference
- blocked companies
- blocked titles
- preferred countries
- timezone preference beyond explicit evidence

If not supported, mark them missing.

### Promotion gate
Draft promotion must fail clearly if required fields are missing or inconsistent.

### Reimport rule
Reimport must not overwrite explicit user preferences blindly.

---

## Canonical job pipeline rules

### Raw payload rule
Always store raw payloads before normalization.

### Normalization rule
Every source must map into one canonical Job model.

### Dedupe rule
Dedupe must be layered, not naive.

Expected order:
1. canonical URL
2. source job key
3. normalized company/title/location
4. bounded fuzzy similarity if justified

### Ranking rule
Ranking must be:
- deterministic
- versioned
- explainable
- independent of OpenClaw output

### Review packet rule
Only sanitized packets go into review.
No raw page dumps.
No prompt-injection junk from hostile pages.

### Digest rule
Digest should include only eligible jobs and should be controlled, auditable, and retry-safe.

---

## Coding rules

### General
- Prefer readability over cleverness.
- Keep modules focused.
- Use explicit names.
- Do not hide important logic in giant helper blobs.
- Do not create abstractions for imaginary future scale.

### Python
- Use typed models and validated config.
- Keep handlers/CLI thin.
- Keep business rules in services/domain logic.
- Keep parser logic source-specific where needed.
- Keep OpenClaw integration behind a narrow abstraction.

### Error handling
- No broad `except: pass`.
- No broad `except: continue` unless the failure is explicitly recorded and the behavior is intentionally isolated.
- All failures that affect correctness must be visible in logs/reports.

### Logging
Use structured logging.
Logs should help answer:
- what source ran
- what stage ran
- what succeeded
- what failed
- how many items were seen/inserted/updated/failed

### Database
- SQLite is acceptable for current scope.
- Enable WAL mode.
- Design schema and migrations explicitly.
- Use indexes and constraints intentionally.
- Do not abuse SQLite like a distributed queue.

---

## Testing rules

### Non-negotiable
Do not rely on manual live-site testing as your only proof.

### Required test coverage areas
- profile bootstrap import/extraction/draft/promotion/reimport
- ATS/public board adapters
- PH board adapters
- direct-page extraction
- normalization
- dedupe
- ranking
- review packet safety
- delivery
- feedback
- rerank/reprocess
- reporting/doctor where practical

### Test style
- use fixtures
- mock network calls
- mock OpenClaw calls
- mock delivery calls
- keep tests fast
- prefer meaningful behavioral tests over existence checks

### Dangerous paths that must be tested
- malformed source payloads
- layout drift in board HTML
- duplicate ingestion runs
- hostile prompt-like content in descriptions
- invalid profile drafts
- reimport merge safety
- digest resend safety
- rerank/reprocess idempotency

---

## CLI expectations

The CLI is the operator interface.
Prefer CLI commands over ad hoc scripts.

Expected commands:

- `findmejobs doctor`
- `findmejobs profile import --file <path>`
- `findmejobs profile show-draft`
- `findmejobs profile missing`
- `findmejobs profile validate-draft`
- `findmejobs profile diff`
- `findmejobs profile promote-draft`
- `findmejobs ingest` (optional `--source` filter by config name or adapter kind)
- `findmejobs rank`
- `findmejobs review export` / `findmejobs review import-results` (alias: `review import`)
- `findmejobs digest send` (and `digest resend` where applicable)
- `findmejobs report`
- `findmejobs feedback`
- `findmejobs rerank`
- `findmejobs reprocess`

Dry-run modes should exist for destructive or external-output flows where practical.

---

## Operational rules

### Local
Use local dev for:
- coding
- tests
- profile bootstrap validation
- parser debugging
- CLI validation

### EC2
Use EC2 for:
- scheduled runs
- systemd services/timers
- persistent SQLite file
- delivery execution
- OpenClaw-connected daily operation

### systemd over bash loops
Do not use infinite `while true; sleep ...` scripts for production scheduling.
Use systemd services/timers.

---

## Anti-patterns explicitly rejected

Do not reintroduce any of this:

- shell `curl` hacks as core fetch logic
- CSV flat-file persistence as system state
- manual comma-splitting for critical data
- exact-link-only dedupe
- naive substring-only ranking
- broad silent exception swallowing
- OpenClaw seeing raw hostile page content
- review logic mixed into scraper/parser code
- giant single-file script architecture
- fake AI personalization that is not auditable

---

## When changing the system

When modifying this repo:

1. Preserve the trust boundary.
2. Preserve deterministic ranking.
3. Preserve idempotency.
4. Preserve observability.
5. Update tests.
6. Update config/docs/examples if behavior changes.
7. Do not expand into deferred / “what’s coming” scope without explicit approval.

## What a good change looks like

- improves correctness
- improves observability
- improves operator usefulness
- keeps the system boring
- keeps failures diagnosable
- adds tests for dangerous paths
- does not secretly widen scope

## What a bad change looks like

- adds complexity without operational value
- hides failures
- mixes review and scraping concerns
- introduces undocumented source-specific hacks
- bypasses validation
- creates opaque ranking behavior
- turns one-host software into fake distributed architecture

---

## Agent instructions

If you are an AI coding agent working in this repo:

- read this file first
- stay within **Available features** unless the operator explicitly approves **What’s coming / deferred**
- do not treat OpenClaw as the raw scraper
- do not invent unsupported user preferences
- do not make ranking depend on LLM output
- do not widen scope silently
- when uncertain, choose the more boring and auditable path
- prefer fixing confirmed defects over broad speculative refactors
- explain tradeoffs clearly and bluntly
- call out brittle parsing honestly
- keep outputs concrete and implementation-ready

When producing plans or code:
- state how the change fits existing features and what remains intentionally deferred
- include validation/test implications
- preserve command-line operability
- preserve source classification and trust weighting
