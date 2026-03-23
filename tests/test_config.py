from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from findmejobs.config.loader import load_app_config, load_profile_config, load_source_configs


def _write_sources_yaml(path: Path, entries: list[str]) -> None:
    path.write_text("\n".join(["version: v1", "sources:", *entries]), encoding="utf-8")


def test_valid_config_loads_correctly(runtime_config_files: tuple[Path, Path, Path]) -> None:
    app_path, profile_path, sources_dir = runtime_config_files
    app_config = load_app_config(app_path)
    profile = load_profile_config(profile_path)
    sources = load_source_configs(sources_dir)

    assert app_config.database.url.startswith("sqlite:///")
    assert profile.version == "test-profile"
    assert {source.kind for source in sources} == {"rss", "greenhouse"}


def test_invalid_source_config_fails_clearly(tmp_path: Path) -> None:
    sources_path = tmp_path / "sources.yaml"
    _write_sources_yaml(
        sources_path,
        [
            "  - name: bad",
            "    kind: not-real",
            "    enabled: true",
        ],
    )

    with pytest.raises(ValidationError) as exc:
        load_source_configs(sources_path)

    assert "not-real" in str(exc.value)


def test_yaml_profile_loads_even_when_some_fields_are_missing(tmp_path: Path) -> None:
    profile_path = tmp_path / "profile.yaml"
    ranking_path = tmp_path / "ranking.yaml"
    profile_path.write_text('{"version":"v1"}', encoding="utf-8")
    ranking_path.write_text('{"rank_model_version":"r1","minimum_score":30.0,"stale_days":30}', encoding="utf-8")

    profile = load_profile_config(profile_path)
    assert profile.version == "v1"
    assert profile.target_titles == []


def test_yaml_profile_pair_loads_correctly(tmp_path: Path) -> None:
    profile_path = tmp_path / "profile.yaml"
    ranking_path = tmp_path / "ranking.yaml"
    profile_path.write_text(
        '{"version":"bootstrap-v1","full_name":"Jane Doe","target_titles":["Backend Engineer"],"required_skills":["Python"],"preferred_locations":["Remote"],"allowed_countries":["PH"]}',
        encoding="utf-8",
    )
    ranking_path.write_text(
        '{"rank_model_version":"bootstrap-v1","stale_days":30,"minimum_score":45.0,"require_remote":true,"remote_first":true,"preferred_companies":["Acme"],"preferred_timezones":["Asia/Manila"],"title_families":{"backend engineer":["software engineer"]},"weights":{"title_alignment":30.0,"must_have_skills":35.0,"preferred_skills":10.0,"location_fit":10.0,"remote_fit":10.0,"recency":5.0}}',
        encoding="utf-8",
    )

    profile = load_profile_config(profile_path)

    assert profile.target_titles == ["Backend Engineer"]
    assert profile.ranking.require_remote is True
    assert profile.ranking.remote_first is True
    assert profile.ranking.preferred_companies == ["Acme"]
    assert profile.ranking.preferred_timezones == ["Asia/Manila"]
    assert profile.ranking.title_families["backend engineer"] == ["software engineer"]


def test_ph_board_source_configs_load_with_lower_default_trust(tmp_path: Path) -> None:
    sources_path = tmp_path / "sources.yaml"
    _write_sources_yaml(
        sources_path,
        [
            "  - name: jobstreet-ph",
            "    kind: jobstreet_ph",
            "    enabled: true",
            "    board_url: https://api.example.test/jobstreet",
            "  - name: kalibrr-ph",
            "    kind: kalibrr",
            "    enabled: true",
            "    board_url: https://api.example.test/kalibrr",
            "  - name: bossjob-ph",
            "    kind: bossjob_ph",
            "    enabled: true",
            "    board_url: https://api.example.test/bossjob",
            "  - name: foundit-ph",
            "    kind: foundit_ph",
            "    enabled: true",
            "    board_url: https://api.example.test/foundit",
        ],
    )

    sources = load_source_configs(sources_path)
    trust_weights = {source.kind: source.trust_weight for source in sources}

    assert trust_weights["jobstreet_ph"] < 1.0
    assert trust_weights["kalibrr"] < 1.0
    assert trust_weights["bossjob_ph"] < 1.0
    assert trust_weights["foundit_ph"] < 1.0


def test_workable_source_config_loads_with_expected_defaults(tmp_path: Path) -> None:
    sources_path = tmp_path / "sources.yaml"
    _write_sources_yaml(
        sources_path,
        [
            "  - name: workable-main",
            "    kind: workable",
            "    enabled: true",
            "    account_subdomain: example",
        ],
    )

    source = load_source_configs(sources_path)[0]

    assert source.kind == "workable"
    assert source.trust_weight == 1.0
    assert source.priority == 0
    assert source.include_details is True


def test_breezy_hr_and_jobvite_source_configs_load_with_expected_defaults(tmp_path: Path) -> None:
    sources_path = tmp_path / "sources.yaml"
    _write_sources_yaml(
        sources_path,
        [
            "  - name: breezy-main",
            "    kind: breezy_hr",
            "    enabled: true",
            "    company_subdomain: example",
            "  - name: jobvite-main",
            "    kind: jobvite",
            "    enabled: true",
            "    company_code: example",
        ],
    )

    sources = {source.kind: source for source in load_source_configs(sources_path)}

    assert sources["breezy_hr"].trust_weight == 1.0
    assert sources["breezy_hr"].priority == 0
    assert sources["jobvite"].trust_weight == 1.0
    assert sources["jobvite"].priority == 0


def test_ph_board_source_configs_accept_explicit_priority_trust_and_fetch_cap(tmp_path: Path) -> None:
    sources_path = tmp_path / "sources.yaml"
    _write_sources_yaml(
        sources_path,
        [
            "  - name: jobstreet-ph",
            "    kind: jobstreet_ph",
            "    enabled: false",
            "    priority: 7",
            "    trust_weight: 0.6",
            "    fetch_cap: 25",
            "    board_url: https://api.example.test/jobstreet",
        ],
    )

    source = load_source_configs(sources_path)[0]

    assert source.enabled is False
    assert source.priority == 7
    assert source.trust_weight == 0.6
    assert source.fetch_cap == 25


@pytest.mark.parametrize(
    "lines",
    [
        [
            'name = "jobstreet-ph"',
            'kind = "jobstreet_ph"',
            "priority = -1",
            'board_url = "https://api.example.test/jobstreet"',
        ],
        [
            'name = "kalibrr-ph"',
            'kind = "kalibrr"',
            "trust_weight = 0",
            'board_url = "https://api.example.test/kalibrr"',
        ],
        [
            'name = "bossjob-ph"',
            'kind = "bossjob_ph"',
            "fetch_cap = 0",
            'board_url = "https://api.example.test/bossjob"',
        ],
        [
            'name = "foundit-ph"',
            'kind = "foundit_ph"',
            'unexpected = "nope"',
            'board_url = "https://api.example.test/foundit"',
        ],
    ],
)
def test_ph_board_source_configs_fail_clearly_on_invalid_values(tmp_path: Path, lines: list[str]) -> None:
    sources_path = tmp_path / "sources.yaml"
    _write_sources_yaml(sources_path, [f"  - {lines[0]}", *[f"    {line}" for line in lines[1:]]])

    with pytest.raises(ValidationError):
        load_source_configs(sources_path)


def test_app_config_rejects_inline_smtp_password(tmp_path: Path) -> None:
    app_path = tmp_path / "app.toml"
    app_path.write_text(
        "\n".join(
            [
                "[database]",
                'url = "sqlite:///./var/app.db"',
                "",
                "[storage]",
                'root_dir = "./var"',
                'raw_dir = "./var/raw"',
                'review_outbox_dir = "./var/review/outbox"',
                'review_inbox_dir = "./var/review/inbox"',
                'lock_dir = "./var/locks"',
                "",
                "[delivery.email]",
                "enabled = true",
                'host = "smtp.example.test"',
                'sender = "noreply@example.test"',
                'recipient = "user@example.test"',
                'password = "plaintext-not-allowed"',
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError) as exc:
        load_app_config(app_path)
    assert "FINDMEJOBS_SMTP_PASSWORD" in str(exc.value)
