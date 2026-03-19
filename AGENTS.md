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
- Runtime paths: `config/app.toml`, `config/profile.yaml`, `config/ranking.yaml`, `config/sources.yaml` (operator-local; gitignored—templates under `config/examples/`)
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

## Adding sources (existing `kind` vs new adapter)

Operators (and OpenClaw driving the CLI) add **config** with `findmejobs sources add`. That only works when `kind` is already implemented in Python. If the target site needs a **new** integration shape, an agent must **add an adapter in this repo** first—OpenClaw must **not** become the scraper of record (see core rule 1).

### When an existing `kind` is enough

1. Build one JSON object that matches `SourceConfig` for a supported `kind` (same fields as `config/examples/sources.yaml` and `src/findmejobs/config/models.py`).
2. Run `findmejobs sources add --json '<object>'` (or `--json-file`).
3. Run `findmejobs doctor`, then `findmejobs ingest --source <name>`.

OpenClaw may assemble the JSON from operator-provided URLs, board tokens, and labels. It must **not** fetch or parse raw job pages on behalf of the pipeline; the app does that on `ingest`.

### When a new adapter is required

Use this when there is **no** `kind` (and no existing adapter) that can fetch and parse that source correctly—for example a new public API shape or a new board HTML layout.

**OpenClaw / coding agents may implement the adapter here** (edit Python, run tests). Do **not** instruct OpenClaw to “just crawl the site” instead of adding `ingestion` code; that violates the trust boundary.

Implementation checklist (keep changes boring and testable):

1. **Classify the source** (Tier A / B / C per [Source classification](#source-classification)). If the new `kind` fits an existing family, add it to the appropriate set in `src/findmejobs/domain/source.py` (`PREDICTABLE_ATS_KINDS`, `PH_BOARD_KINDS`, `DIRECT_PAGE_KINDS`, or `DISCOVERY_KINDS`). If it does not fit yet, leave it unlisted (metrics may show `unknown` until you classify it).
2. **Config model** — In `src/findmejobs/config/models.py`, define a new `*SourceConfig` subclass of `SourceBaseConfig` with `kind: Literal["your_kind"]` and the fields the adapter needs (URLs, tokens, etc.). Append it to the `SourceConfig` discriminated union.
3. **Adapter** — Add `src/findmejobs/ingestion/adapters/<your_kind>.py` implementing `SourceAdapter`: `build_url(config) -> str` and `parse(artifact, config) -> list[SourceJobRecord]` (use `parse_with_stats` when you need skip accounting). Use `validate_config_type` from `ingestion/adapters/base.py` against your config class.
4. **Orchestrator** — In `src/findmejobs/ingestion/orchestrator.py`, import the new config and adapter classes and add a branch in `build_adapter()` that returns your adapter for that config type.
5. **Example (optional)** — Add a commented or disabled example entry under `config/examples/sources.yaml` for operators copying templates.
6. **Tests** — Add adapter tests with **mocked HTTP** and fixture payloads: happy path, malformed payloads, and layout drift where relevant (see [Testing rules](#testing-rules)). Tier B–style sources need stronger parser tests.
7. **Ship the source config** — Run `findmejobs sources add --json '...'` with the new `kind`; then `doctor` and `ingest`.

After this, OpenClaw chat can add **additional** sources of the same `kind` using `sources add` only—no further Python changes unless the site’s contract changes.

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
- Committed templates: `config/examples/app.toml`, `config/examples/sources.yaml`, and draft examples under `config/examples/`
- Operator-local (gitignored): `config/app.toml`, `config/profile.yaml`, `config/ranking.yaml`, `config/sources.yaml`
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
- `findmejobs sources list` / `findmejobs sources add` / `sources set` / `sources disable` / `sources remove` (validated `sources.yaml` writes)
- `findmejobs rank`
- `findmejobs ranking explain` / `findmejobs ranking set` (inspect or patch scalar/list/weights/title-family `ranking.yaml` fields)
- `findmejobs jobs list` (ranked job previews for the current profile)
- `findmejobs review export` / `findmejobs review import-results` (alias: `review import`)
- `findmejobs digest send` (and `digest resend` where applicable)
- `findmejobs report`
- `findmejobs feedback`
- `findmejobs rerank`
- `findmejobs reprocess`

Dry-run modes should exist for destructive or external-output flows where practical.

---

## Minimal CLI surface (target)

**Intent:** Let operators (and OpenClaw driving the CLI) update **validated** config without ad hoc YAML/TOML surgery. **Shipped:** canonical YAML runtime (`profile.yaml`, `ranking.yaml`, `sources.yaml`), `config init` / `config validate` / `config show-effective`, `sources list/add/set/disable/remove`, `ranking set` (scalar/list/weights/title-family), and profile bootstrap/promote.

### Principles

1. **Every write path:** load → Pydantic validate → persist → operator runs `doctor` (or the command runs an inline subset of those checks).
2. **Prefer small verbs** over dumping arbitrary config trees. OpenClaw may pass flags or a **single** bounded `--json` blob for one resource (e.g. one source), not unbounded free-form YAML.
3. **Secrets:** do not require SMTP passwords on the CLI (shell history). Prefer env vars or a gitignored secrets file; CLI only toggles non-secret delivery fields.
4. **Lists:** support explicit **add / remove / replace** semantics so behavior stays deterministic and auditable.

### Tier 1 — highest impact

#### `findmejobs sources`

| Command | Purpose |
|--------|---------|
| `sources list [--json]` | **Done.** Enumerate validated entries in `config/sources.yaml`. |
| `sources add --json '<object>'` or `--json-file <path>` | **Done.** Validate one JSON object as `SourceConfig`, append/replace one entry in `config/sources.yaml`. |
| `sources set <name>` | **Target.** Patch only `SourceBaseConfig` scalars: `enabled`, `priority`, `trust_weight`, `fetch_cap`; optional `--add-blocked-title-keyword` / `--remove-blocked-title-keyword`. |
| `sources remove <name> [--yes]` | **Target.** Delete the file, or defer and document **disable-only** if delete is too sharp for v1. |

**Why:** Ingest depends on sources; validated `add` lets OpenClaw chat drive new feeds without hand-written TOML.

#### Extend `findmejobs ranking set`

Keep current scalar flags. Add validated operations for lists, weights, and `title_families` (backed by `RankingConfigDraft` / `RankingWeights`):

| Addition | Example shape |
|----------|----------------|
| Weights | `--weight-title-alignment 25` (one flag per `RankingWeights` field) |
| String lists | `--add-blocked-company`, `--remove-blocked-company`; same pattern for `blocked_title_keywords`, `allowed_companies`, `preferred_companies`, `preferred_timezones` |
| `title_families` | `--title-family-add <family> --pattern "..."` (repeatable); `--title-family-remove` / `--title-family-clear <family>` |

#### `findmejobs profile set` (new command)

Canonical `config/profile.yaml`, validated as `ProfileConfig`:

| Pattern | Fields |
|--------|--------|
| Scalars | `--full-name`, `--headline`, `--email`, and other string/int fields aligned with `ProfileConfig` |
| Lists | `--add-target-title` / `--remove-target-title`; same for skills, locations, `allowed_countries`, `strengths`, etc. |
| Optional batch replace | `--set-target-titles-json '[...]'` with **documented replace-entire-list** semantics (for OpenClaw batch updates) |

**Guardrails:** `target_titles` is required—do not allow empty without an explicit escape hatch. Emit a short post-write summary or diff hint.

### Tier 2 — `app.toml` without secrets

#### `findmejobs app set` (narrow)

Non-secret operational fields from `AppConfig` only, e.g.:

- `logging.level`
- `http.timeout_seconds`, `http.max_attempts`, `http.user_agent`
- `delivery.daily_hour`, `delivery.digest_max_items`
- `delivery.email.enabled`, host, port, username, sender, recipient, `use_tls` (password via env only)

**Defer or warn heavily:** changing `storage.*` paths (breaks DB/raw layout expectations).

### Tier 3 — convenience

| Command | Purpose |
|--------|---------|
| `findmejobs config init` | Copy from `config/examples/` when files are missing (today’s manual `cp` ritual). |
| `findmejobs config validate` | Config-load checks without full `doctor` / DB work (optional split). |

### Explicit non-goals

- Arbitrary TOML/YAML patch (`yq`-style) that bypasses models.
- LLM-driven or non-deterministic ranking changes.
- Defining **new** adapter `kind` values without code—CLI configures only kinds the app already implements.

### Suggested implementation order

1. `sources list`, `sources add` (**done**); then `sources set` (enable + base scalars).
2. Extend `ranking set` with list, weight, and `title_family` operations.
3. `profile set` (add/remove lists + key scalars).
4. `app set` (non-secret only) + documented env-based secrets.
5. `config init` when onboarding polish matters.

### Testing expectations

- Temp dir fixtures: run command → reload via existing loaders (`load_source_configs`, `load_profile_config`, ranking load/patch).
- Failure cases: duplicate source `name`, invalid `kind`, bad URL, empty `target_titles`, invalid JSON on optional replace flags.

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
- for new sources: use **`sources add`** when `kind` already exists; if a new `kind` is needed, follow **[Adding sources (existing `kind` vs new adapter)](#adding-sources-existing-kind-vs-new-adapter)** (implement adapter in Python, then config via CLI)—never substitute chat scraping for `ingestion`
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
