#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="$ROOT_DIR/var/ui-data"
CLI_BIN="${FINDMEJOBS_BIN:-$ROOT_DIR/.venv/bin/findmejobs}"

if [[ ! -x "$CLI_BIN" ]]; then
  echo "error: findmejobs CLI not executable at $CLI_BIN" >&2
  echo "set FINDMEJOBS_BIN to your CLI path" >&2
  exit 1
fi

mkdir -p "$OUT_DIR"

"$CLI_BIN" config show-effective --json > "$OUT_DIR/config.json"
"$CLI_BIN" ranking explain --json > "$OUT_DIR/ranking.json"
"$CLI_BIN" sources list --json > "$OUT_DIR/sources.json"
"$CLI_BIN" jobs list --json --all-scored --limit 500 > "$OUT_DIR/jobs.json"
"$CLI_BIN" report > "$OUT_DIR/report.json"

date -u +"%Y-%m-%dT%H:%M:%SZ" > "$OUT_DIR/generated_at.txt"

echo "wrote UI data to $OUT_DIR"
