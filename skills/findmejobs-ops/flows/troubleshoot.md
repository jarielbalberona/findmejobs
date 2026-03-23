# Troubleshooting

1. **`findmejobs status --json`** — overall `ok`, config/profile/ranking readiness, latest pipeline rows, review/application counts, `warnings`, `errors`, `artifacts.paths`.
2. **`findmejobs doctor --json`** — structured errors; check `summary.hints` for `no_enabled_sources` / `pipeline_never_succeeded`.
3. **`findmejobs config validate --json`** — load failures for app/profile/sources.
4. **Queues** — `review queue --json`, `applications queue --json`, `jobs top --limit 20 --json`.

## Exit codes

- `status` exits non-zero when `ok` is false (same as `doctor` pattern): treat as “needs attention,” not necessarily a crashed install.

## Common fixes

- No enabled sources in DB: `sources add`, then successful `ingest`.
- Profile not loadable: fix `profile.yaml` + `ranking.yaml` or complete bootstrap promote.
- Review backlog: run `review export`; after OpenClaw, `review import-results`.
