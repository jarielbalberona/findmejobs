# Profile refresh

Goal: update canonical profile and ranking using app commands, not ad hoc YAML surgery.

## Bootstrap (draft) path

```bash
findmejobs profile import --file /path/to/resume.pdf
findmejobs profile show-draft
findmejobs profile missing
findmejobs profile validate-draft
findmejobs profile promote-draft
```

## Canonical tweaks

```bash
findmejobs profile set --add-target-title "Staff Engineer" --json
```

Use `findmejobs ranking set --help` for validated patches to `ranking.yaml` (text output), then `findmejobs rank --json` to refresh scores.

## Read canonical state

```bash
findmejobs profile show --json
findmejobs ranking show --json
findmejobs ranking explain --json
```
