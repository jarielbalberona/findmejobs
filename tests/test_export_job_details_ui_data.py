from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path


def _write_app_config(path: Path, database_url: str) -> None:
    path.write_text(f'[database]\nurl = "{database_url}"\n', encoding="utf-8")


def test_export_job_details_handles_unmigrated_sqlite_without_traceback(project_root: Path, tmp_path: Path) -> None:
    db_path = tmp_path / "empty.db"
    sqlite3.connect(db_path).close()
    app_path = tmp_path / "app.toml"
    out_path = tmp_path / "job_details.json"
    _write_app_config(app_path, f"sqlite:///{db_path}")
    script = project_root / "scripts" / "export_job_details_ui_data.py"

    result = subprocess.run(
        [sys.executable, str(script), "--app-config-path", str(app_path), "--out", str(out_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["jobs"] == {}
    assert any(str(item).startswith("missing_required_tables:") for item in payload["warnings"])


def test_export_job_details_succeeds_when_schema_exists(
    project_root: Path,
    tmp_path: Path,
    migrated_db_url: str,
) -> None:
    app_path = tmp_path / "app.toml"
    out_path = tmp_path / "job_details.json"
    _write_app_config(app_path, migrated_db_url)
    script = project_root / "scripts" / "export_job_details_ui_data.py"

    result = subprocess.run(
        [sys.executable, str(script), "--app-config-path", str(app_path), "--out", str(out_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["warnings"] == []
    assert payload["jobs"] == {}
