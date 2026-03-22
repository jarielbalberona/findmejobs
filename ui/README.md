# findmejobs local UI (read-only)

This UI is static HTML/CSS/JS and reads snapshot JSON files from `var/ui-data`.

## 1) Export real data from the app

From repo root:

```bash
./scripts/export_ui_data.sh
```

This writes:

- `var/ui-data/config.json`
- `var/ui-data/ranking.json`
- `var/ui-data/sources.json`
- `var/ui-data/jobs.json`
- `var/ui-data/report.json`
- `var/ui-data/generated_at.txt`

## 2) Serve repo root and open the UI

```bash
python3 -m http.server 4173
```

Open:

- `http://127.0.0.1:4173/ui/`

The page fetches data from `/var/ui-data/*.json`.

## 3) Refresh data after pipeline changes

Re-run:

```bash
./scripts/export_ui_data.sh
```

Then click `Reload Data` in the UI.

## Notes

- View-only by design. No write actions.
- If `jobs.json` is empty, run `findmejobs rank` and export again.
