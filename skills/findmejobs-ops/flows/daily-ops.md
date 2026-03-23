# Daily operations

Goal: ingest → rank → review export → digest (only when enabled or forced).

## Commands

```bash
findmejobs daily-run --json
findmejobs daily-run --dry-run --json
findmejobs daily-run --send-digest --json   # send even if delivery.email.enabled is false
findmejobs daily-run --skip-digest --json   # never run digest send
```

## Behavior

- Each step is a real CLI invocation; failures stop the sequence and appear in `summary.steps` with `exit_code` and parsed `envelope` when stdout was JSON.
- UI data export is **not** run unless individual commands pass `--export-ui-data`.
