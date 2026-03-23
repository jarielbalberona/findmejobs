# Onboarding (first-time)

Goal: validate or create config, confirm storage paths, surface draft vs canonical profile state, run `doctor`, optionally import a resume when the operator provides a file.

## Commands

```bash
findmejobs onboarding run --json
findmejobs onboarding run --dry-run --json
findmejobs onboarding run --resume-file /path/to/resume.pdf --json
```

## Notes

- Does **not** auto-import a resume unless `--resume-file` is set.
- After a successful import path, the operator still runs `profile validate-draft` / `profile promote-draft` as needed (see `profile-refresh.md`).
- If `config validate` fails, the workflow may run `config init` once under `--config-root`, then re-validate.
