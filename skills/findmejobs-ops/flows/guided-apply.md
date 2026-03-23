# Guided apply

Goal: open a bounded browser-assisted apply session, fill obvious fields, and stop before irreversible actions.

## Commands

```bash
findmejobs apply prepare --job-id <job_id> --json
findmejobs apply open --job-id <job_id> --mode guided --json
findmejobs apply status --job-id <job_id> --json
findmejobs apply report --job-id <job_id> --json
```

## Rules

- Assume login is already handled manually in the browser session if the site requires it.
- Guided mode is step-by-step. Do not infer permission to continue past risky or ambiguous states.
- Final submit is blocked by design. The operator reviews and clicks submit manually.
- Unknown questions, conflicting prefilled values, and missing uploads stay unresolved until the operator decides.
