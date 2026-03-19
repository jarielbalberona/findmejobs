# Contributing

This project is a boring, single-host Python pipeline on purpose. Keep it that way.

If your contribution adds complexity without improving correctness, observability, or operator usefulness, it is probably bad and should not be merged.

## Ground Rules

- Preserve the trust boundary. Raw fetched content stays inside ingestion and normalization. OpenClaw only sees sanitized review or drafting packets.
- Preserve deterministic ranking. Do not make ranking depend on LLM output.
- Preserve idempotency. Ingest, normalize, dedupe, rank, review, digest, rerank, and reprocess must stay safe to rerun.
- Keep failures visible. No silent parser failure paths and no broad `except: pass`.
- Stay inside current scope. Do not slip **deferred / out-of-scope** work (see [README.md](README.md) **What's coming** and [AGENTS.md](AGENTS.md) **What's coming / deferred**) into an unrelated PR.

## Local Setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head
pytest
```

## The Two Ways To Add Sources

There are only two legitimate cases:

1. Add a new source config for an existing adapter kind.
2. Add a brand-new adapter kind.

Do not build custom one-off hacks in random scripts. The operator interface is the CLI and the runtime config under `config/sources.d/`.

## Case 1: Add A Source Using An Existing Adapter

If the site already fits one of the shipped kinds, do not write new parser code. Add a TOML config file instead.

Current kinds:

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

Start from the matching shipped template under `config/examples/sources.d/`, copy it into **`config/sources.d/`** (gitignored), then edit:

```toml
name = "acme-greenhouse"
kind = "greenhouse"
enabled = true
company_name = "Acme"
board_token = "acme"
include_content = true
```

Run it directly:

```bash
findmejobs ingest --source acme-greenhouse
findmejobs report
findmejobs doctor
```

What matters:

- `name` is the local operator identifier.
- `kind` selects the adapter.
- `company_name` is a fallback, not an excuse to guess.
- `enabled = true` is required if you want the CLI to run it.
- `trust_weight` should reflect reality. PH boards are not as trustworthy as predictable ATS APIs.

If an existing adapter works, that is the correct boring solution.

## Case 2: Add A Brand-New Adapter Kind

Do this only when the source does not fit an existing kind cleanly.

### Step 1: Classify The Source First

Before writing code, decide what it is:

- Tier A predictable ATS/public structured source
- Tier B PH board parser
- Tier C generic direct-page fallback
- RSS/feed-style discovery source

If you cannot classify it cleanly, you probably do not understand it well enough to merge it.

Do not add:

- LinkedIn scraping
- Playwright/browser automation
- credentialed scraping
- CAPTCHA handling
- auto-submit flows

That matches the **What's coming / deferred** list in [AGENTS.md](AGENTS.md) and [README.md](README.md) and is out of bounds unless explicitly approved.

### Step 2: Add The Typed Config Model

Add a new Pydantic config model in [src/findmejobs/config/models.py](/Volumes/Files/softwareengineering/my-projects/findmejobs/src/findmejobs/config/models.py) and include it in the `SourceConfig` discriminator union.

Rules:

- Keep fields explicit.
- Use `extra="forbid"` behavior from `SourceBaseConfig`.
- Set sane defaults only when they are actually sane.
- Do not add vague catch-all blobs.

### Step 3: Implement The Adapter

Add a focused adapter in [src/findmejobs/ingestion/adapters](/Volumes/Files/softwareengineering/my-projects/findmejobs/src/findmejobs/ingestion/adapters).

Follow the existing contract in [src/findmejobs/ingestion/adapters/base.py](/Volumes/Files/softwareengineering/my-projects/findmejobs/src/findmejobs/ingestion/adapters/base.py):

- `build_url(config)` returns the fetch target.
- `parse(artifact, config)` returns `list[SourceJobRecord]`.
- `parse_with_stats(...)` is required when partial layout drift matters and skipped items must be observable.

Hard rules:

- Keep fetch logic out of the adapter. The orchestrator fetches, stores raw payloads, and calls the adapter.
- Return only `SourceJobRecord` values. Do not touch the database from adapter code.
- Fail visibly on malformed payloads. Raise explicit `ValueError` codes rather than silently returning garbage.
- Do not send raw hostile HTML or page dumps across the review boundary.
- Do not mix ranking or review logic into parsing.

### Step 4: Wire It Into The Runtime

Update the adapter selection path in [src/findmejobs/ingestion/orchestrator.py](/Volumes/Files/softwareengineering/my-projects/findmejobs/src/findmejobs/ingestion/orchestrator.py).

If the new kind changes source-family classification, update [src/findmejobs/domain/source.py](/Volumes/Files/softwareengineering/my-projects/findmejobs/src/findmejobs/domain/source.py) as well.

If you skip classification wiring, source trust and reporting will be wrong. That is a defect, not a minor omission.

### Step 5: Add Example Config

Add a matching **committed** example under `config/examples/sources.d/` (not under `config/sources.d/`, which is gitignored for operators).

That example should be enough for another contributor to understand the required fields without reading the adapter implementation.

### Step 6: Add Real Tests

This is non-negotiable. New source work without tests is low-value scrap.

Minimum expected coverage:

- realistic fixture parse success
- malformed payload failure
- missing required fields
- partial layout drift or skipped item accounting where applicable
- normalization safety for hostile HTML or weak fields

Relevant test locations:

- [tests/test_fetch_and_adapters.py](/Volumes/Files/softwareengineering/my-projects/findmejobs/tests/test_fetch_and_adapters.py)
- [tests/test_ph_board_adapters.py](/Volumes/Files/softwareengineering/my-projects/findmejobs/tests/test_ph_board_adapters.py)
- [tests/fixtures](/Volumes/Files/softwareengineering/my-projects/findmejobs/tests/fixtures)

Use fixtures. Mock network calls. Do not treat live-site manual checks as proof.

### Step 7: Verify End-To-End

At minimum, run the targeted tests and a CLI ingest for the source you changed:

```bash
pytest tests/test_fetch_and_adapters.py tests/test_ph_board_adapters.py
findmejobs ingest --source your-source-name
findmejobs report
```

If your change touches normalization, ranking inputs, dedupe behavior, config loading, or review safety, run the broader affected test set too.

## Parser Quality Bar

Source adapters are not allowed to be optimistic fiction.

A mergeable adapter should:

- extract stable identifiers
- produce canonical URLs where possible
- avoid guessing employer names unless a configured fallback exists
- capture raw payloads through the normal ingest path
- record skipped items when payload drift is partial rather than total
- degrade safely when salary, location, or posted date are missing or malformed

A non-mergeable adapter usually does one of these:

- scrapes an unstable page with no tests
- hides failures behind empty results
- requires credentials or browser automation
- mixes review concerns into fetching/parsing
- adds site-specific hacks without documenting them

## Pull Request Checklist

Before opening a PR, verify all of this:

- code stays inside the current scope boundary (not **What's coming / deferred** unless the PR is explicitly about that)
- trust boundary remains intact
- ranking remains deterministic
- parser failures are visible
- raw payload capture still happens before normalization
- example config was added or updated
- fixtures and tests were added or updated
- README or docs were updated if contributor-facing behavior changed

If you are adding a source and you cannot explain its classification, failure modes, and test coverage in plain English, the work is not ready.
