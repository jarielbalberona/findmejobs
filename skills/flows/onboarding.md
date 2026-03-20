## Onboarding (Deterministic)

1. `.venv/bin/findmejobs config init --json`
2. `.venv/bin/findmejobs config validate --json`
3. `.venv/bin/findmejobs profile import --file <resume_path>`
4. `.venv/bin/findmejobs profile validate-draft`
5. `.venv/bin/findmejobs profile promote-draft`
6. `.venv/bin/findmejobs sources add --json '<source-object>'`
7. `.venv/bin/findmejobs ingest` (needed so SQLite gets enabled `sources` rows and a successful `pipeline_runs` row)
8. `.venv/bin/findmejobs doctor --json`

`doctor` before the first successful ingest often reports `no_enabled_sources` and/or `pipeline_never_succeeded`. That is expected: it reads the database (enabled source rows and last successful pipeline), not `config init` alone. Read the printed **Why / what to do** lines (or the `hints` object with `--json`).
