---
name: findmejobs-ops
description: Operate the findmejobs CLI predictably for OpenClaw—status, composed onboarding/daily runs, queues, and canonical config inspection using --json envelopes.
metadata: {"openclaw":{"os":["darwin","linux"]}}
---

# findmejobs operator (OpenClaw)

Use **app-owned CLI commands** only. Do not edit `config/*.yaml` or TOML by hand unless the operator explicitly asks; prefer `config validate`, `profile set`, `sources add|set`, `ranking set`, etc.

## Defaults for agents

- Prefer **`--json`** on every read-heavy or agent-facing command. Parse the stable envelope (see `AGENTS.md`).
- Treat **canonical runtime config** as the source of truth: `config/app.toml`, `config/profile.yaml`, `config/ranking.yaml`, `config/sources.yaml` (operator-local; templates under `config/examples/`).
- Do not scrape job sites from chat; the Python app owns ingestion and normalization.

## Command-first flow (happy path)

1. `findmejobs onboarding run --json` (use `--dry-run` to plan only; add `--resume-file` only when the operator supplies a path)
2. `findmejobs status --json`
3. `findmejobs daily-run --json` (use `--dry-run` to plan; `--send-digest` to force digest when email is disabled)
4. `findmejobs review queue --json`
5. `findmejobs jobs top --limit 20 --json`

## Inspection without reading files

- `findmejobs profile show --json` — merged canonical profile
- `findmejobs ranking show --json` — canonical `ranking.yaml` as structured JSON
- `findmejobs applications queue --json` — application drafting backlog

## Troubleshooting

1. `findmejobs status --json` — snapshot of config, pipeline recency, queue counts
2. `findmejobs doctor --json` — DB, paths, sources, pipeline health (see `summary.hints` when present)
3. `findmejobs config validate --json`
4. Queue commands above for review/application backlogs

## Runbooks

- `flows/onboarding.md`
- `flows/daily-ops.md`
- `flows/profile-refresh.md`
- `flows/troubleshoot.md`

## Examples

- `examples/commands/` — copy/paste command lines
- `examples/json/` — sample `--json` envelopes (illustrative)

This skill stays thin: **no business logic here**—only how to drive the installed CLI safely.
