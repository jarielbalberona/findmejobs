from __future__ import annotations

import json
from pathlib import Path

from findmejobs.cli.app import app
from findmejobs.profile_bootstrap.models import ResumeExtractionDraft


class ResultClient:
    def __init__(self, request_path: Path, result_path: Path) -> None:
        self.request_path = request_path
        self.result_path = result_path

    def export_request(self, packet) -> Path:
        self.request_path.parent.mkdir(parents=True, exist_ok=True)
        self.request_path.write_text(packet.model_dump_json(indent=2), encoding="utf-8")
        return self.request_path

    def load_result_text(self):
        expected_import_id = json.loads(self.request_path.read_text(encoding="utf-8"))["import_id"]
        return ResumeExtractionDraft(
            import_id=expected_import_id,
            full_name="Jane Doe",
            headline="Senior Backend Engineer",
            email="jane@example.com",
            location_text="Manila, Philippines",
            target_titles=["Backend Engineer"],
            required_skills=["Python"],
            preferred_skills=["SQL"],
            preferred_locations=["Remote"],
            explicit_fields=[],
        ).model_dump_json(indent=2)


def test_profile_cli_flow(
    cli_runner,
    fixtures_dir: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state" / "profile_bootstrap"
    config_root = tmp_path / "config"
    monkeypatch.setattr(
        "findmejobs.profile_bootstrap.service.FilesystemProfileBootstrapOpenClawClient",
        ResultClient,
    )

    result = cli_runner.invoke(
        app,
        [
            "profile",
            "import",
            "--file",
            str(fixtures_dir / "resume.txt"),
            "--state-root",
            str(state_root),
            "--config-root",
            str(config_root),
        ],
    )
    assert result.exit_code == 0
    assert "pending=False" in result.stdout

    result = cli_runner.invoke(
        app,
        ["profile", "show-draft", "--state-root", str(state_root), "--config-root", str(config_root)],
    )
    assert result.exit_code == 0
    assert "Jane Doe" in result.stdout

    result = cli_runner.invoke(
        app,
        ["profile", "missing", "--state-root", str(state_root), "--config-root", str(config_root)],
    )
    assert result.exit_code == 0

    result = cli_runner.invoke(
        app,
        ["profile", "validate-draft", "--state-root", str(state_root), "--config-root", str(config_root)],
    )
    assert result.exit_code == 0
    assert "status=strong" in result.stdout

    result = cli_runner.invoke(
        app,
        ["profile", "diff", "--state-root", str(state_root), "--config-root", str(config_root)],
    )
    assert result.exit_code == 0
    assert "safe_auto_updates" in result.stdout

    result = cli_runner.invoke(
        app,
        ["profile", "promote-draft", "--state-root", str(state_root), "--config-root", str(config_root)],
    )
    assert result.exit_code == 0
    assert (config_root / "profile.yaml").exists()
    assert (config_root / "ranking.yaml").exists()


def test_profile_cli_reimport_writes_diff(
    cli_runner,
    fixtures_dir: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state" / "profile_bootstrap"
    config_root = tmp_path / "config"
    monkeypatch.setattr(
        "findmejobs.profile_bootstrap.service.FilesystemProfileBootstrapOpenClawClient",
        ResultClient,
    )

    first = cli_runner.invoke(
        app,
        [
            "profile",
            "import",
            "--file",
            str(fixtures_dir / "resume.txt"),
            "--state-root",
            str(state_root),
            "--config-root",
            str(config_root),
        ],
    )
    assert first.exit_code == 0

    promoted = cli_runner.invoke(
        app,
        ["profile", "promote-draft", "--state-root", str(state_root), "--config-root", str(config_root)],
    )
    assert promoted.exit_code == 0

    second = cli_runner.invoke(
        app,
        [
            "profile",
            "reimport",
            "--file",
            str(fixtures_dir / "resume.md"),
            "--state-root",
            str(state_root),
            "--config-root",
            str(config_root),
        ],
    )
    assert second.exit_code == 0
    assert (state_root / "drafts" / "reimport_diff.yaml").exists()


def test_profile_cli_import_fails_for_unsupported_file(
    cli_runner,
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state" / "profile_bootstrap"
    config_root = tmp_path / "config"
    bad_file = tmp_path / "resume.html"
    bad_file.write_text("<html></html>", encoding="utf-8")

    result = cli_runner.invoke(
        app,
        [
            "profile",
            "import",
            "--file",
            str(bad_file),
            "--state-root",
            str(state_root),
            "--config-root",
            str(config_root),
        ],
    )
    assert result.exit_code == 1
    assert "unsupported resume format" in result.stdout


def test_profile_cli_import_without_file_refreshes_pending_result(
    cli_runner,
    fixtures_dir: Path,
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state" / "profile_bootstrap"
    config_root = tmp_path / "config"

    first = cli_runner.invoke(
        app,
        [
            "profile",
            "import",
            "--file",
            str(fixtures_dir / "resume.txt"),
            "--state-root",
            str(state_root),
            "--config-root",
            str(config_root),
        ],
    )
    assert first.exit_code == 0
    assert "pending=True" in first.stdout

    meta = json.loads((state_root / "extracted" / "resume.meta.json").read_text(encoding="utf-8"))
    (state_root / "review" / "openclaw_result.json").write_text(
        ResumeExtractionDraft(
            import_id=meta["import_id"],
            full_name="Jane Doe",
            target_titles=["Backend Engineer"],
            required_skills=["Python"],
            preferred_locations=["Remote"],
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )

    second = cli_runner.invoke(
        app,
        ["profile", "import", "--state-root", str(state_root), "--config-root", str(config_root)],
    )
    assert second.exit_code == 0
    assert "pending=False" in second.stdout


def test_profile_cli_validate_fails_for_incomplete_draft(
    cli_runner,
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state" / "profile_bootstrap"
    config_root = tmp_path / "config"
    drafts = state_root / "drafts"
    drafts.mkdir(parents=True, exist_ok=True)
    (drafts / "profile.draft.yaml").write_text(
        '{"version":"bootstrap-v1","target_titles":[],"required_skills":[],"preferred_skills":[],"preferred_locations":[],"allowed_countries":[]}',
        encoding="utf-8",
    )
    (drafts / "ranking.draft.yaml").write_text(
        '{"rank_model_version":"bootstrap-v1","minimum_score":45.0,"stale_days":30}',
        encoding="utf-8",
    )
    (drafts / "missing_fields.yaml").write_text(
        '{"missing":[{"field":"target_titles","reason":"required","required_for_promotion":true}],"low_confidence_fields":[]}',
        encoding="utf-8",
    )

    result = cli_runner.invoke(
        app,
        ["profile", "validate-draft", "--state-root", str(state_root), "--config-root", str(config_root)],
    )
    assert result.exit_code == 1
    assert "profile draft invalid" in result.stdout


def test_profile_cli_import_rejects_both_answers_inputs(
    cli_runner,
    fixtures_dir: Path,
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state" / "profile_bootstrap"
    config_root = tmp_path / "config"
    answers_file = tmp_path / "answers.txt"
    answers_file.write_text("Prefer remote.", encoding="utf-8")

    result = cli_runner.invoke(
        app,
        [
            "profile",
            "import",
            "--file",
            str(fixtures_dir / "resume.txt"),
            "--answers-file",
            str(answers_file),
            "--answers-text",
            "Prefer remote.",
            "--state-root",
            str(state_root),
            "--config-root",
            str(config_root),
        ],
    )
    assert result.exit_code == 1
    assert "provide either answers_file or answers_text" in result.stdout
