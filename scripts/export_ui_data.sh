#!/usr/bin/env bash
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="$ROOT_DIR/var/ui-data"
CLI_BIN="${FINDMEJOBS_BIN:-$ROOT_DIR/.venv/bin/findmejobs}"
APP_CONFIG_PATH="${FINDMEJOBS_APP_CONFIG_PATH:-config/app.toml}"
PROFILE_PATH="${FINDMEJOBS_PROFILE_PATH:-config/profile.yaml}"
SOURCES_PATH="${FINDMEJOBS_SOURCES_PATH:-config/sources.yaml}"

if [[ ! -x "$CLI_BIN" ]]; then
  echo "error: findmejobs CLI not executable at $CLI_BIN" >&2
  echo "set FINDMEJOBS_BIN to your CLI path" >&2
  exit 1
fi

mkdir -p "$OUT_DIR"

WARNINGS=()

run_json_command() {
  local name="$1"
  shift
  local out_file="$OUT_DIR/$name.json"
  local output
  if output="$("$@" 2>&1)"; then
    printf '%s\n' "$output" > "$out_file"
  else
    WARNINGS+=("$name")
    python3 - "$name" "$output" "$out_file" <<'PY'
import json
import sys
name, output, out_file = sys.argv[1], sys.argv[2], sys.argv[3]
payload = {"status": "error", "command": name, "message": output.strip() or "command failed"}
if len(payload["message"]) > 1500:
    payload["message"] = payload["message"][:1500] + "... [truncated]"
with open(out_file, "w", encoding="utf-8") as fh:
    json.dump(payload, fh, indent=2)
    fh.write("\n")
PY
  fi
}

run_json_command "config" "$CLI_BIN" config show-effective --json --app-config-path "$APP_CONFIG_PATH" --profile-path "$PROFILE_PATH" --sources-path "$SOURCES_PATH"
run_json_command "ranking" "$CLI_BIN" ranking explain --json --profile-path "$PROFILE_PATH"
run_json_command "sources" "$CLI_BIN" sources list --json --sources-path "$SOURCES_PATH"
run_json_command "jobs" "$CLI_BIN" jobs list --json --all-scored --limit 500 --app-config-path "$APP_CONFIG_PATH" --profile-path "$PROFILE_PATH" --sources-path "$SOURCES_PATH"
run_json_command "report" "$CLI_BIN" report --app-config-path "$APP_CONFIG_PATH" --profile-path "$PROFILE_PATH" --sources-path "$SOURCES_PATH"

if ! python3 "$ROOT_DIR/scripts/export_application_ui_data.py" --state-root "$ROOT_DIR/state/applications" --out "$OUT_DIR/application.json"; then
  WARNINGS+=("application")
fi

date -u +"%Y-%m-%dT%H:%M:%SZ" > "$OUT_DIR/generated_at.txt"

if [[ ${#WARNINGS[@]} -eq 0 ]]; then
  echo "wrote UI data to $OUT_DIR"
else
  echo "wrote UI data to $OUT_DIR (warnings: ${WARNINGS[*]})"
fi
