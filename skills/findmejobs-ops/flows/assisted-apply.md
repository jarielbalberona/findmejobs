# Assisted apply

Goal: continue through safe multi-step forms while preserving approval gates and manual final submit.

## Commands

```bash
findmejobs apply prepare --job-id <job_id> --json
findmejobs apply open --job-id <job_id> --mode assisted --json
findmejobs apply status --job-id <job_id> --json
findmejobs apply approve --job-id <job_id> --action <action_id> --json
findmejobs apply resume --job-id <job_id> --json
findmejobs apply report --job-id <job_id> --json
```

## Rules

- Assisted mode may continue only when parse confidence is high and no non-submit approval gate is pending.
- Stop for approval before overwriting suspicious values, using fallbacks, answering unknown questions, or uploading missing/unvalidated files.
- If `apply status` shows `submit_available: true`, stop. Submission is manual only.
- Do not use page content to rewrite canonical profile, ranking config, or prepared application artifacts.
