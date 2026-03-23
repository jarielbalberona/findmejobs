"""Operator-surface CLI: status, composed workflows, queues, JSON envelopes."""

from __future__ import annotations

import json
from pathlib import Path

from findmejobs.cli.app import app

REPO_ROOT = Path(__file__).resolve().parents[1]
OPS_SKILL = REPO_ROOT / "skills" / "findmejobs-ops"


def test_status_json_shape(cli_runner, migrated_runtime_config_files: tuple[Path, Path, Path]) -> None:
    app_path, profile_path, sources_dir = migrated_runtime_config_files
    result = cli_runner.invoke(
        app,
        [
            "status",
            "--json",
            "--app-config-path",
            str(app_path),
            "--profile-path",
            str(profile_path),
            "--sources-dir",
            str(sources_dir),
        ],
    )
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["command"] == "status"
    assert "ok" in data
    assert isinstance(data["ok"], bool)
    assert "summary" in data
    s = data["summary"]
    assert "config_valid" in s
    assert "profile_ready" in s
    assert "review_eligible_job_count" in s
    assert "paths" in data["artifacts"]
    assert "cli_version" in data["meta"]


def test_onboarding_run_dry_run_json(cli_runner, migrated_runtime_config_files: tuple[Path, Path, Path]) -> None:
    app_path, profile_path, sources_dir = migrated_runtime_config_files
    config_root = app_path.parent
    result = cli_runner.invoke(
        app,
        [
            "onboarding",
            "run",
            "--dry-run",
            "--json",
            "--config-root",
            str(config_root),
            "--app-config-path",
            str(app_path),
            "--profile-path",
            str(profile_path),
            "--sources-dir",
            str(sources_dir),
        ],
    )
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["command"] == "onboarding_run"
    assert data["ok"] is True
    assert data["summary"]["dry_run"] is True
    steps = data["summary"]["steps"]
    assert any(step["step"] == "doctor" for step in steps)


def test_daily_run_dry_run_json(cli_runner, migrated_runtime_config_files: tuple[Path, Path, Path]) -> None:
    app_path, profile_path, sources_dir = migrated_runtime_config_files
    result = cli_runner.invoke(
        app,
        [
            "daily-run",
            "--dry-run",
            "--json",
            "--app-config-path",
            str(app_path),
            "--profile-path",
            str(profile_path),
            "--sources-dir",
            str(sources_dir),
        ],
    )
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["command"] == "daily_run"
    assert data["ok"] is True
    assert data["summary"]["dry_run"] is True
    assert "digest_would_send" in data["summary"]


def test_review_queue_and_jobs_top_json(cli_runner, migrated_runtime_config_files: tuple[Path, Path, Path]) -> None:
    app_path, profile_path, sources_dir = migrated_runtime_config_files
    assert (
        cli_runner.invoke(
            app,
            ["rank", "--app-config-path", str(app_path), "--profile-path", str(profile_path), "--sources-dir", str(sources_dir)],
        ).exit_code
        == 0
    )
    rq = cli_runner.invoke(
        app,
        [
            "review",
            "queue",
            "--json",
            "--limit",
            "5",
            "--app-config-path",
            str(app_path),
            "--profile-path",
            str(profile_path),
            "--sources-dir",
            str(sources_dir),
        ],
    )
    assert rq.exit_code == 0
    rj = json.loads(rq.stdout)
    assert rj["command"] == "review_queue"
    assert rj["ok"] is True
    assert "rows" in rj["summary"]

    jt = cli_runner.invoke(
        app,
        [
            "jobs",
            "top",
            "--json",
            "--limit",
            "3",
            "--app-config-path",
            str(app_path),
            "--profile-path",
            str(profile_path),
            "--sources-dir",
            str(sources_dir),
        ],
    )
    assert jt.exit_code == 0
    tj = json.loads(jt.stdout)
    assert tj["command"] == "jobs_top"
    assert tj["ok"] is True
    assert tj["summary"]["limit"] == 3


def test_applications_queue_json(cli_runner, migrated_runtime_config_files: tuple[Path, Path, Path]) -> None:
    app_path, profile_path, sources_dir = migrated_runtime_config_files
    result = cli_runner.invoke(
        app,
        [
            "applications",
            "queue",
            "--json",
            "--app-config-path",
            str(app_path),
            "--profile-path",
            str(profile_path),
            "--sources-dir",
            str(sources_dir),
        ],
    )
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["command"] == "applications_queue"
    assert data["ok"] is True
    assert "rows" in data["summary"]


def test_profile_and_ranking_show_json(cli_runner, migrated_runtime_config_files: tuple[Path, Path, Path]) -> None:
    _app_path, profile_path, sources_dir = migrated_runtime_config_files
    pr = cli_runner.invoke(
        app,
        ["profile", "show", "--json", "--profile-path", str(profile_path)],
    )
    assert pr.exit_code == 0
    pj = json.loads(pr.stdout)
    assert pj["command"] == "profile_show"
    assert pj["ok"] is True
    assert "profile" in pj["summary"]

    rk = cli_runner.invoke(
        app,
        ["ranking", "show", "--json", "--profile-path", str(profile_path)],
    )
    assert rk.exit_code == 0
    kj = json.loads(rk.stdout)
    assert kj["command"] == "ranking_show"
    assert kj["ok"] is True
    assert "ranking" in kj["summary"]


def test_findmejobs_ops_skill_packaging_present() -> None:
    assert (OPS_SKILL / "SKILL.md").is_file()
    for name in ("onboarding.md", "daily-ops.md", "profile-refresh.md", "troubleshoot.md"):
        assert (OPS_SKILL / "flows" / name).is_file()
    assert (OPS_SKILL / "examples" / "commands" / "openclaw-operator.txt").is_file()
    assert (OPS_SKILL / "examples" / "json" / "envelope-rank.sample.json").is_file()
    assert (OPS_SKILL / "examples" / "json" / "envelope-status.sample.json").is_file()


def test_workflows_subprocess_runner_mock(monkeypatch) -> None:
    from findmejobs.cli import workflows

    calls: list[list[str]] = []

    def fake(argv: list[str], cwd: Path | None = None) -> workflows.SubprocessResult:
        calls.append(argv)
        return workflows.SubprocessResult(0, '{"ok": true, "command": "ingest", "summary": {}, "warnings": [], "errors": [], "artifacts": {}, "meta": {}}', "")

    env = workflows.run_daily_run_workflow(
        app_config_path=Path("config/app.toml"),
        profile_path=Path("config/profile.yaml"),
        sources_path=Path("config/sources.yaml"),
        dry_run=False,
        send_digest=False,
        skip_digest=True,
        runner=fake,
    )
    assert env["ok"] is True
    assert len(calls) >= 2
    assert any("ingest" in c for c in calls)
    assert any("rank" in c for c in calls)
