from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import sessionmaker
from typer.testing import CliRunner

from findmejobs.db.session import create_session_factory


@pytest.fixture(scope="session")
def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def fixtures_dir(project_root: Path) -> Path:
    return project_root / "tests" / "fixtures"


@pytest.fixture()
def cli_runner() -> CliRunner:
    return CliRunner()


def upgrade_test_database(project_root: Path, database_url: str) -> None:
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


@pytest.fixture()
def migrated_db_url(tmp_path: Path, project_root: Path) -> str:
    database_url = f"sqlite:///{tmp_path / 'app.db'}"
    upgrade_test_database(project_root, database_url)
    return database_url


@pytest.fixture()
def session_factory(migrated_db_url: str) -> sessionmaker:
    return create_session_factory(migrated_db_url)


@pytest.fixture()
def runtime_paths(tmp_path: Path) -> dict[str, Path]:
    base = tmp_path / "runtime"
    return {
        "base": base,
        "etc": base / "etc",
        "sources": base / "etc" / "sources.d",
        "var": base / "var",
        "raw": base / "var" / "raw",
        "outbox": base / "var" / "review" / "outbox",
        "inbox": base / "var" / "review" / "inbox",
        "locks": base / "var" / "locks",
        "db": base / "var" / "app.db",
    }


def write_runtime_config(
    runtime_paths: dict[str, Path],
    *,
    rss_enabled: bool = True,
    greenhouse_enabled: bool = True,
    rss_url: str = "https://example.test/jobs.rss",
    greenhouse_token: str = "acme",
) -> tuple[Path, Path, Path]:
    runtime_paths["base"].mkdir(parents=True, exist_ok=True)
    runtime_paths["etc"].mkdir(parents=True, exist_ok=True)
    runtime_paths["sources"].mkdir(parents=True, exist_ok=True)
    runtime_paths["var"].mkdir(parents=True, exist_ok=True)
    runtime_paths["raw"].mkdir(parents=True, exist_ok=True)
    runtime_paths["outbox"].mkdir(parents=True, exist_ok=True)
    runtime_paths["inbox"].mkdir(parents=True, exist_ok=True)
    runtime_paths["locks"].mkdir(parents=True, exist_ok=True)
    runtime_paths["db"].parent.mkdir(parents=True, exist_ok=True)

    app_path = runtime_paths["etc"] / "app.toml"
    profile_path = runtime_paths["etc"] / "profile.toml"
    sources_dir = runtime_paths["sources"]
    app_path.write_text(
        "\n".join(
            [
                "[database]",
                f'url = "sqlite:///{runtime_paths["db"]}"',
                "",
                "[storage]",
                f'root_dir = "{runtime_paths["var"]}"',
                f'raw_dir = "{runtime_paths["raw"]}"',
                f'review_outbox_dir = "{runtime_paths["outbox"]}"',
                f'review_inbox_dir = "{runtime_paths["inbox"]}"',
                f'lock_dir = "{runtime_paths["locks"]}"',
                "",
                "[http]",
                "timeout_seconds = 0.1",
                "max_attempts = 3",
                'user_agent = "pytest-findmejobs"',
                "",
                "[logging]",
                'level = "INFO"',
            ]
        ),
        encoding="utf-8",
    )
    profile_path.write_text(
        "\n".join(
            [
                'version = "test-profile"',
                'rank_model_version = "test-rank-model"',
                'target_titles = ["Backend Engineer", "Python Engineer"]',
                'required_skills = ["python", "sql"]',
                'preferred_skills = ["aws", "fastapi"]',
                'preferred_locations = ["remote", "philippines"]',
                'allowed_countries = ["PH", "US"]',
                "",
                "[ranking]",
                "stale_days = 30",
                "minimum_score = 30.0",
                "minimum_salary = 90000",
                'blocked_companies = ["Reject Co"]',
                'blocked_title_keywords = ["intern"]',
                "require_remote = true",
                "",
                "[ranking.weights]",
                "title_alignment = 30.0",
                "must_have_skills = 35.0",
                "preferred_skills = 10.0",
                "location_fit = 10.0",
                "remote_fit = 10.0",
                "recency = 5.0",
            ]
        ),
        encoding="utf-8",
    )
    (sources_dir / "rss.toml").write_text(
        "\n".join(
            [
                'name = "rss-source"',
                'kind = "rss"',
                f'enabled = {"true" if rss_enabled else "false"}',
                f'feed_url = "{rss_url}"',
            ]
        ),
        encoding="utf-8",
    )
    (sources_dir / "greenhouse.toml").write_text(
        "\n".join(
            [
                'name = "acme"',
                'kind = "greenhouse"',
                f'enabled = {"true" if greenhouse_enabled else "false"}',
                f'board_token = "{greenhouse_token}"',
                "include_content = true",
            ]
        ),
        encoding="utf-8",
    )
    return app_path, profile_path, sources_dir


@pytest.fixture()
def runtime_config_files(runtime_paths: dict[str, Path]) -> tuple[Path, Path, Path]:
    return write_runtime_config(runtime_paths)


@pytest.fixture()
def migrated_runtime_config_files(
    runtime_config_files: tuple[Path, Path, Path],
    runtime_paths: dict[str, Path],
    project_root: Path,
) -> tuple[Path, Path, Path]:
    upgrade_test_database(project_root, f"sqlite:///{runtime_paths['db']}")
    return runtime_config_files
