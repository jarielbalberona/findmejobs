from __future__ import annotations

import json
import logging
import os
import subprocess
from collections import Counter
import shutil
from pathlib import Path
from typing import Annotated, Any

import typer
from typer import Context
from pydantic import ValidationError

from findmejobs import __version__ as FINDMEJOBS_VERSION
from sqlalchemy import select

from findmejobs.application.service import ApplicationDraftService
from findmejobs.apply.browser import ApplyBrowserRunner, build_browser_backend
from findmejobs.apply.service import ApplySessionService
from findmejobs.apply.openclaw import FilesystemApplyOpenClawClient
from findmejobs.config.loader import ensure_directories, load_app_config, load_profile_config, load_source_configs, resolve_profile_config_path
from findmejobs.config.models import RankingWeights, SourceConfig, SourcesFileConfig
from findmejobs.config.source_file import (
    add_source,
    disable_source,
    list_sources,
    load_sources_file,
    parse_source_json_payload,
    remove_source,
    set_source_fields,
    write_sources_file,
)
from findmejobs.db.models import ApplicationSubmission, Digest, JobCluster, JobClusterMember, JobScore, NormalizedJob, Source, SourceJob
from findmejobs.db.repositories import (
    create_application_submission,
    create_job_feedback,
    create_pipeline_run,
    finish_pipeline_run,
    update_application_submission,
    upsert_job_score,
    upsert_profile,
    upsert_rank_model,
)
from findmejobs.db.session import create_session_factory
from findmejobs.delivery.digest import send_digest
from findmejobs.domain.job import CanonicalJob
from findmejobs.domain.source import SourceJobRecord
from findmejobs.feedback import ALLOWED_FEEDBACK_TYPES, feedback_types_for_job, record_feedback
from findmejobs.ingestion.orchestrator import run_ingest
from findmejobs.normalization.canonicalize import normalize_job
from findmejobs.observability.doctor import (
    check_profile_config_health,
    doctor_failure_hints,
    quality_gate_failures,
    run_doctor,
)
from findmejobs.observability.job_listing import fetch_job_previews, format_job_previews_text
from findmejobs.observability.logging import configure_logging
from findmejobs.observability.reporting import build_report
from findmejobs.profile_bootstrap.promote import load_existing_ranking
from findmejobs.profile_bootstrap.service import ProfileBootstrapService
from findmejobs.profile_bootstrap.models import ProfileConfigDraft, RankingConfigDraft
from findmejobs.ranking.engine import rank_job_with_feedback
from findmejobs.ranking.audit import resolve_ranking_audit_fixture, run_ranking_audit
from findmejobs.ranking.explain import build_ranking_explain_payload, format_ranking_explain_text
from findmejobs.ranking.yaml_patch import patch_ranking_yaml
from findmejobs.review.service import export_review_packets, import_review_packets
from findmejobs.utils.ids import new_id
from findmejobs.utils.locking import FileLock
from findmejobs.utils.time import utcnow
from findmejobs.utils.yamlio import dump_yaml, load_yaml

from findmejobs.cli.json_envelope import cli_envelope, emit_envelope, meta_standard
from findmejobs.cli.operator_queues import fetch_applications_queue_rows, fetch_review_queue_rows
from findmejobs.cli.operator_status import build_operator_status
from findmejobs.cli.workflows import run_daily_run_workflow, run_onboarding_workflow

app = typer.Typer(
    help="Single-host job intelligence CLI. Typical flow: ingest → rank → review export → digest send.",
    no_args_is_help=True,
    epilog=(
        "Command groups: config, onboarding, review, profile, ranking, digest, feedback, reprocess, jobs, "
        "applications, sources — plus top-level ingest, rank, status, daily-run, doctor, report."
    ),
)
config_app = typer.Typer(help="Config initialization, validation, and effective resolved values")
review_app = typer.Typer(help="Sanitized review packets (export to outbox, import results from inbox)")
profile_app = typer.Typer(help="Profile bootstrap from resume → draft → promote")
digest_app = typer.Typer(help="Email digest send / resend")
feedback_app = typer.Typer(help="Operator feedback on jobs/clusters")
reprocess_app = typer.Typer(help="Re-run normalization or review packet rebuilds")
ranking_app = typer.Typer(help="Inspect or adjust deterministic ranking (config/ranking.yaml)")
jobs_app = typer.Typer(help="Inspect stored jobs (ranked previews for the current profile)")
sources_app = typer.Typer(help="Add/list/update validated source definitions in config/sources.yaml")
submissions_app = typer.Typer(help="Track human-triggered application submissions and outcomes")
onboarding_app = typer.Typer(help="First-time operator setup checks (composed CLI steps)")
applications_app = typer.Typer(help="Per-job application drafting state (read-only queues)")
apply_app = typer.Typer(help="Browser-assisted application sessions with explicit approval gates")
app.add_typer(config_app, name="config")
app.add_typer(review_app, name="review")
app.add_typer(profile_app, name="profile")
app.add_typer(digest_app, name="digest")
app.add_typer(feedback_app, name="feedback")
app.add_typer(reprocess_app, name="reprocess")
app.add_typer(ranking_app, name="ranking")
app.add_typer(jobs_app, name="jobs")
app.add_typer(sources_app, name="sources")
app.add_typer(submissions_app, name="submissions")
app.add_typer(onboarding_app, name="onboarding")
app.add_typer(applications_app, name="applications")
app.add_typer(apply_app, name="apply")


def _version_option(value: bool) -> None:
    if value:
        typer.echo(FINDMEJOBS_VERSION)
        raise typer.Exit(0)


@app.callback()
def _root_options(
    ctx: Context,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            callback=_version_option,
            is_eager=True,
            help="Show version and exit.",
        ),
    ] = False,
) -> None:
    pass


def _typer_group_show_help_callback(ctx: Context) -> None:
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit(0)


config_app.callback(invoke_without_command=True)(_typer_group_show_help_callback)
review_app.callback(invoke_without_command=True)(_typer_group_show_help_callback)
profile_app.callback(invoke_without_command=True)(_typer_group_show_help_callback)
digest_app.callback(invoke_without_command=True)(_typer_group_show_help_callback)
feedback_app.callback(invoke_without_command=True)(_typer_group_show_help_callback)
reprocess_app.callback(invoke_without_command=True)(_typer_group_show_help_callback)
ranking_app.callback(invoke_without_command=True)(_typer_group_show_help_callback)
jobs_app.callback(invoke_without_command=True)(_typer_group_show_help_callback)
sources_app.callback(invoke_without_command=True)(_typer_group_show_help_callback)
submissions_app.callback(invoke_without_command=True)(_typer_group_show_help_callback)
onboarding_app.callback(invoke_without_command=True)(_typer_group_show_help_callback)
applications_app.callback(invoke_without_command=True)(_typer_group_show_help_callback)
apply_app.callback(invoke_without_command=True)(_typer_group_show_help_callback)

LOGGER = logging.getLogger(__name__)
SUBMISSION_STATUSES = {"submitted", "interview", "rejected", "offer", "withdrawn"}
SUBMISSION_FEEDBACK_MAP = {
    "submitted": "applied",
    "interview": "interview",
    "rejected": "rejected",
    "offer": "offer",
    "withdrawn": "withdrawn",
}


def _load_runtime(app_config_path: Path, profile_path: Path, sources_path: Path):
    app_config = load_app_config(app_config_path)
    profile = load_profile_config(resolve_profile_config_path(profile_path))
    sources = load_source_configs(sources_path)
    ensure_directories(
        [
            app_config.storage.root_dir,
            app_config.storage.raw_dir,
            app_config.storage.review_outbox_dir,
            app_config.storage.review_inbox_dir,
            app_config.storage.lock_dir,
        ]
    )
    configure_logging(app_config.logging.level)
    session_factory = create_session_factory(app_config.database.url)
    return app_config, profile, sources, session_factory


def _emit_json(json_out: bool, payload: dict, text: str | None = None) -> None:
    if json_out:
        typer.echo(json.dumps(payload, indent=2, default=str))
        return
    if text is not None:
        typer.echo(text)


def _emit_standard_envelope(
    json_out: bool,
    command: str,
    ok: bool,
    *,
    summary: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
    artifacts: dict[str, Any] | None = None,
    meta_extra: dict[str, Any] | None = None,
    text: str | None = None,
) -> None:
    meta = meta_standard()
    if meta_extra:
        meta.update(meta_extra)
    env = cli_envelope(
        command,
        ok,
        summary=summary or {},
        warnings=warnings or [],
        errors=errors or [],
        artifacts=artifacts or {},
        meta=meta,
    )
    emit_envelope(json_out, env, text=text)


def _artifacts_for_ui_export(
    enabled: bool,
    *,
    app_config_path: Path,
    profile_path: Path | None = None,
    sources_path: Path | None = None,
) -> dict[str, Any]:
    if not enabled:
        return {"status": "skipped", "message": "export_ui_data_not_requested", "script_path": None}
    return dict(_run_ui_data_export_script(app_config_path, profile_path=profile_path, sources_path=sources_path))


def _run_ui_data_export_script(
    app_config_path: Path,
    *,
    profile_path: Path | None = None,
    sources_path: Path | None = None,
) -> dict[str, object]:
    env_script = os.getenv("FINDMEJOBS_UI_EXPORT_SCRIPT")
    script_candidates: list[Path] = []
    if env_script:
        script_candidates.append(Path(env_script).expanduser())
    script_candidates.append(Path.cwd() / "scripts" / "export_ui_data.sh")
    script_candidates.append(app_config_path.resolve().parent.parent / "scripts" / "export_ui_data.sh")

    script_path: Path | None = next((candidate for candidate in script_candidates if candidate.exists()), None)
    if script_path is None:
        return {
            "status": "skipped",
            "message": (
                "ui data export skipped: scripts/export_ui_data.sh not found "
                "(checked FINDMEJOBS_UI_EXPORT_SCRIPT, cwd/scripts, and app-config-root/scripts)"
            ),
            "script_path": None,
        }

    result = subprocess.run(
        [str(script_path)],
        cwd=str(script_path.parent.parent),
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "FINDMEJOBS_APP_CONFIG_PATH": str(app_config_path),
            **({"FINDMEJOBS_PROFILE_PATH": str(profile_path)} if profile_path is not None else {}),
            **({"FINDMEJOBS_SOURCES_PATH": str(sources_path)} if sources_path is not None else {}),
        },
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        stdout = result.stdout.strip()
        detail = stderr or stdout or f"exit_code={result.returncode}"
        return {
            "status": "failed",
            "message": f"ui data export failed: {detail}",
            "script_path": str(script_path),
        }

    out = result.stdout.strip()
    return {
        "status": "ok",
        "message": out or "ui data export complete",
        "script_path": str(script_path),
    }


def _echo_ui_export_status(ui_export: dict[str, object] | None, *, enabled: bool) -> None:
    if ui_export is None:
        return
    if not enabled:
        typer.echo("ui data export: skipped (pass --export-ui-data to run scripts/export_ui_data.sh)")
        return
    if ui_export.get("status") == "ok":
        typer.echo(f"ui export: {ui_export.get('message')}")
        return
    typer.echo(f"warning: {ui_export.get('message')}")


def _pipeline_lock_path(app_config) -> Path:
    return app_config.storage.lock_dir / "pipeline.lock"


def _filter_source_configs(sources: list[SourceConfig], source_tokens: list[str]) -> list[SourceConfig]:
    """Keep configs whose name or adapter kind matches any non-empty token."""
    want = {t.strip() for t in source_tokens if t.strip()}
    if not want:
        return sources
    return [s for s in sources if s.name in want or s.kind in want]


def _canonical_job_from_row(row: NormalizedJob, source: Source | None = None) -> CanonicalJob:
    return CanonicalJob(
        source_job_id=row.source_job_id,
        source_id=source.id if source is not None else "",
        source_job_key="",
        source_name=source.name if source is not None else None,
        source_trust_weight=source.trust_weight if source is not None else 1.0,
        source_priority=source.priority if source is not None else 0,
        canonical_url=row.canonical_url,
        company_name=row.company_name,
        title=row.title,
        location_text=row.location_text,
        location_type=row.location_type,
        country_code=row.country_code,
        city=row.city,
        region=row.region,
        seniority=row.seniority,
        employment_type=row.employment_type,
        salary_min=row.salary_min,
        salary_max=row.salary_max,
        salary_currency=row.salary_currency,
        salary_period=row.salary_period,
        description_text=row.description_text,
        tags=row.tags_json,
        posted_at=row.posted_at,
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
        normalization_errors=row.normalization_errors_json,
    )


@config_app.command("init")
def config_init(
    config_root: Path = typer.Option(Path("config"), exists=False, file_okay=False),
    force: bool = typer.Option(False, "--force", help="Overwrite existing config files"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    app_path = config_root / "app.toml"
    profile_path = config_root / "profile.yaml"
    ranking_path = config_root / "ranking.yaml"
    sources_path = config_root / "sources.yaml"
    examples_root = config_root / "examples"
    targets: list[tuple[Path, Path | None, str]] = [
        (app_path, examples_root / "app.toml", "app"),
        (profile_path, examples_root / "profile.draft.yaml", "profile"),
        (ranking_path, examples_root / "ranking.draft.yaml", "ranking"),
    ]
    written: list[str] = []
    skipped: list[str] = []
    for path, template, label in targets:
        if path.exists() and not force:
            skipped.append(str(path))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        if template is not None and template.exists():
            shutil.copy2(template, path)
        else:
            path.write_text("", encoding="utf-8")
        written.append(str(path))
    if sources_path.exists() and not force:
        skipped.append(str(sources_path))
    else:
        write_sources_file(sources_path, SourcesFileConfig(version="v1", sources=[]))
        written.append(str(sources_path))
    if json_out:
        _emit_standard_envelope(
            json_out,
            "config_init",
            True,
            summary={"written": written, "skipped": skipped},
            artifacts={"config_root": str(config_root.resolve())},
            text=None,
        )
    else:
        typer.echo(f"config init: wrote={len(written)} skipped={len(skipped)}")


@config_app.command("validate")
def config_validate(
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.yaml")),
    sources_path: Path = typer.Option(Path("config/sources.yaml")),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    errors: list[str] = []
    try:
        app_config = load_app_config(app_config_path)
    except Exception as exc:  # noqa: BLE001
        app_config = None
        errors.append(f"app_config_invalid:{exc}")
    try:
        profile = load_profile_config(profile_path)
    except Exception as exc:  # noqa: BLE001
        profile = None
        errors.append(f"profile_config_invalid:{exc}")
    try:
        sources = load_source_configs(sources_path)
    except Exception as exc:  # noqa: BLE001
        sources = []
        errors.append(f"sources_config_invalid:{exc}")
    ok = not errors
    summary = {
        "app_config_path": str(app_config_path),
        "profile_path": str(profile_path),
        "sources_path": str(sources_path),
        "source_count": len(sources),
        "profile_version": getattr(profile, "version", None),
        "rank_model_version": getattr(profile, "rank_model_version", None),
        "database_url": app_config.database.url if app_config is not None else None,
        "validation_errors": errors,
    }
    if errors:
        _emit_standard_envelope(
            json_out,
            "config_validate",
            False,
            summary=summary,
            errors=errors,
            text=f"config validate failed: {errors}",
        )
        raise typer.Exit(code=1)
    _emit_standard_envelope(json_out, "config_validate", ok, summary=summary, text="config validate: ok")


@config_app.command("show-effective")
def config_show_effective(
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.yaml")),
    sources_path: Path = typer.Option(Path("config/sources.yaml")),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    app_config = load_app_config(app_config_path)
    profile = load_profile_config(profile_path)
    sources = load_source_configs(sources_path)
    paths = {
        "app_config_path": str(app_config_path.resolve()),
        "profile_path": str(profile_path.resolve()),
        "ranking_path": str(profile_path.with_name("ranking.yaml").resolve()),
        "sources_path": str(sources_path.resolve()),
    }
    summary = {
        "app": app_config.model_dump(mode="json"),
        "profile": profile.model_dump(mode="json"),
        "sources": [source.model_dump(mode="json") for source in sources],
    }
    if json_out:
        _emit_standard_envelope(
            json_out,
            "config_show_effective",
            True,
            summary=summary,
            artifacts={"paths": paths},
        )
    else:
        typer.echo(json.dumps({"paths": paths, **summary}, indent=2, default=str))


@app.command()
def status(
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.yaml")),
    sources_path: Path = typer.Option(Path("config/sources.yaml"), "--sources-path", "--sources-dir"),
    applications_state_root: Path = typer.Option(Path("state/applications")),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Single snapshot of operational readiness, pipeline recency, and queue counts."""
    app_config = load_app_config(app_config_path)
    ensure_directories(
        [
            app_config.storage.root_dir,
            app_config.storage.raw_dir,
            app_config.storage.review_outbox_dir,
            app_config.storage.review_inbox_dir,
            app_config.storage.lock_dir,
        ]
    )
    configure_logging(app_config.logging.level)
    session_factory = create_session_factory(app_config.database.url)
    profile = None
    try:
        profile = load_profile_config(resolve_profile_config_path(profile_path))
    except (FileNotFoundError, ValidationError, ValueError):
        pass
    with session_factory() as session:
        snap = build_operator_status(
            session,
            app_config,
            profile=profile,
            app_config_path=app_config_path,
            profile_path=profile_path,
            sources_path=sources_path,
            applications_state_root=applications_state_root,
        )
    ok = bool(snap.pop("ok"))
    warnings = snap.pop("warnings")
    errors = snap.pop("errors")
    paths = snap.pop("paths")
    inner_meta = snap.pop("meta", {})
    if json_out:
        _emit_standard_envelope(
            json_out,
            "status",
            ok,
            summary=snap,
            warnings=warnings,
            errors=errors,
            artifacts={"paths": paths},
            meta_extra=inner_meta,
        )
    else:
        typer.echo(json.dumps({"ok": ok, **snap, "warnings": warnings, "errors": errors, "paths": paths}, indent=2, default=str))
    if not ok:
        raise typer.Exit(code=1)


@app.command("daily-run")
def daily_run(
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.yaml")),
    sources_path: Path = typer.Option(Path("config/sources.yaml"), "--sources-path", "--sources-dir"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    send_digest: bool = typer.Option(False, "--send-digest", help="Send digest even when delivery.email.enabled is false."),
    skip_digest: bool = typer.Option(False, "--skip-digest", help="Do not run digest send."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    env = run_daily_run_workflow(
        app_config_path=app_config_path,
        profile_path=profile_path,
        sources_path=sources_path,
        dry_run=dry_run,
        send_digest=send_digest,
        skip_digest=skip_digest,
    )
    if json_out:
        typer.echo(json.dumps(env, indent=2, default=str))
    else:
        typer.echo(f"daily-run ok={env.get('ok')} step_count={len(env.get('summary', {}).get('steps', []))}")
    if not env.get("ok"):
        raise typer.Exit(code=1)


@onboarding_app.command("run")
def onboarding_run(
    config_root: Path = typer.Option(Path("config"), "--config-root"),
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.yaml")),
    sources_path: Path = typer.Option(Path("config/sources.yaml"), "--sources-path", "--sources-dir"),
    profile_bootstrap_state: Path = typer.Option(Path("state/profile_bootstrap"), "--profile-state-root"),
    resume_file: Path | None = typer.Option(None, "--resume-file", exists=True, dir_okay=False),
    dry_run: bool = typer.Option(False, "--dry-run"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    env = run_onboarding_workflow(
        config_root=config_root,
        app_config_path=app_config_path,
        profile_path=profile_path,
        sources_path=sources_path,
        profile_bootstrap_state=profile_bootstrap_state,
        dry_run=dry_run,
        resume_file=resume_file,
    )
    if json_out:
        typer.echo(json.dumps(env, indent=2, default=str))
    else:
        typer.echo(f"onboarding run ok={env.get('ok')} step_count={len(env.get('summary', {}).get('steps', []))}")
    if not env.get("ok"):
        raise typer.Exit(code=1)


@app.command()
def ingest(
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.yaml")),
    sources_path: Path = typer.Option(
        Path("config/sources.yaml"),
        "--sources-path",
        "--sources-dir",
    ),
    source: Annotated[
        list[str],
        typer.Option(
            "--source",
            help="Run only source configs whose name or kind matches (repeatable). E.g. --source greenhouse or --source acme.",
        ),
    ] = [],
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    app_config, _profile, all_sources, session_factory = _load_runtime(app_config_path, profile_path, sources_path)
    sources = _filter_source_configs(all_sources, source)
    if source:
        if not sources:
            names = ", ".join(sorted({s.name for s in all_sources}))
            message = (
                f"No source configs matched --source {source!r}. "
                f"Use names (or kinds) from source config under {sources_path} (names: {names})."
            )
            _emit_standard_envelope(
                json_out,
                "ingest",
                False,
                summary={"error_code": "no_matching_sources", "message": message},
                errors=[message],
                text=message,
            )
            raise typer.Exit(code=1)
    if sources and not any(s.enabled for s in sources):
        n = len(sources)
        selected = ", ".join(sorted(s.name for s in sources))
        message = (
            f"No enabled sources to run: all {n} matching config(s) are disabled "
            f"(enabled = false under {sources_path}). "
            f"Enable at least one in sources.yaml or adjust --source. "
            f"Matching names: {selected}."
        )
        _emit_standard_envelope(
            json_out,
            "ingest",
            False,
            summary={"error_code": "all_selected_sources_disabled", "message": message},
            errors=[message],
            text=message,
        )
        raise typer.Exit(code=1)
    with FileLock(_pipeline_lock_path(app_config)):
        with session_factory() as session:
            run = create_pipeline_run(session, "ingest", new_id)
            session.commit()
            try:
                counts = run_ingest(session, app_config, sources, new_id)
                if counts["failed_sources"] > 0:
                    error_message = f"{counts['failed_sources']} source(s) failed during ingest"
                    finish_pipeline_run(run, "failed", counts, error_message=error_message)
                    session.commit()
                    _emit_standard_envelope(
                        json_out,
                        "ingest",
                        False,
                        summary={"counts": counts, "error_message": error_message},
                        errors=[error_message],
                        text=f"ingest failed: {counts}",
                    )
                    raise typer.Exit(code=1)
                finish_pipeline_run(run, "success", counts)
                session.commit()
                _emit_standard_envelope(
                    json_out,
                    "ingest",
                    True,
                    summary={"counts": counts},
                    text=f"ingest complete: {counts}",
                )
            except typer.Exit:
                raise
            except Exception as exc:
                finish_pipeline_run(run, "failed", error_message=str(exc))
                session.commit()
                _emit_standard_envelope(
                    json_out,
                    "ingest",
                    False,
                    summary={"error": str(exc)},
                    errors=[str(exc)],
                    text=f"ingest failed: {exc}",
                )
                raise


@app.command()
def rank(
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.yaml")),
    sources_path: Path = typer.Option(Path("config/sources.yaml"), "--sources-path", "--sources-dir"),
    export_ui_data: bool = typer.Option(
        False,
        "--export-ui-data/--no-export-ui-data",
        help="Run scripts/export_ui_data.sh after successful rank (default: off).",
    ),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    app_config, profile, _sources, session_factory = _load_runtime(app_config_path, profile_path, sources_path)
    with FileLock(_pipeline_lock_path(app_config)):
        with session_factory() as session:
            run = create_pipeline_run(session, "rank", new_id)
            session.commit()
            try:
                profile_row = upsert_profile(session, profile, new_id)
                rank_model = upsert_rank_model(session, profile, new_id)
                clusters = session.execute(
                    select(JobCluster, NormalizedJob, Source)
                    .join(NormalizedJob, NormalizedJob.id == JobCluster.representative_job_id)
                    .join(SourceJob, SourceJob.id == NormalizedJob.source_job_id)
                    .join(Source, Source.id == SourceJob.source_id)
                    .where(NormalizedJob.normalization_status == "valid")
                )
                scored = 0
                filtered = 0
                below_minimum = 0
                hard_filter_hits: Counter[str] = Counter()
                for cluster, job_row, source in clusters:
                    feedback_types = feedback_types_for_job(
                        session,
                        cluster_id=cluster.id,
                        company_name=job_row.company_name,
                        title=job_row.title,
                    )
                    breakdown = rank_job_with_feedback(_canonical_job_from_row(job_row, source), profile, feedback_types=feedback_types)
                    upsert_job_score(session, cluster.id, profile_row.id, rank_model.id, breakdown, new_id)
                    scored += 1
                    if breakdown.hard_filter_reasons:
                        filtered += 1
                        hard_filter_hits.update(breakdown.hard_filter_reasons)
                    elif breakdown.total < profile.ranking.minimum_score:
                        below_minimum += 1
                rank_stats = {
                    "scored": scored,
                    "total_scored": scored,
                    "filtered": filtered,
                    "passed_hard_filters": max(scored - filtered, 0),
                    "below_minimum": below_minimum,
                    "model_version": profile.rank_model_version,
                    "hard_filter_reason_counts": dict(sorted(hard_filter_hits.items())),
                }
                finish_pipeline_run(run, "success", rank_stats)
                session.commit()
                ui_art = _artifacts_for_ui_export(
                    export_ui_data,
                    app_config_path=app_config_path,
                    profile_path=profile_path,
                    sources_path=sources_path,
                )
                summary = {
                    "scored": scored,
                    "total_scored": scored,
                    "filtered": filtered,
                    "passed_hard_filters": max(scored - filtered, 0),
                    "below_minimum": below_minimum,
                    "model_version": profile.rank_model_version,
                    "hard_filter_reason_counts": dict(sorted(hard_filter_hits.items())),
                }
                _emit_standard_envelope(
                    json_out,
                    "rank",
                    True,
                    summary=summary,
                    artifacts={"ui_export": ui_art},
                    text=f"rank complete: scored={scored} filtered={filtered}",
                )
                if not json_out:
                    if hard_filter_hits:
                        parts = ", ".join(f"{reason}={count}" for reason, count in sorted(hard_filter_hits.items()))
                        typer.echo(
                            "hard filter reasons (hits; a job with multiple reasons adds to each): "
                            + parts
                        )
                    _echo_ui_export_status(ui_art, enabled=export_ui_data)
            except typer.Exit:
                raise
            except Exception as exc:
                finish_pipeline_run(run, "failed", error_message=str(exc))
                session.commit()
                _emit_standard_envelope(
                    json_out,
                    "rank",
                    False,
                    summary={"error": str(exc)},
                    errors=[str(exc)],
                    text=f"rank failed: {exc}",
                )
                raise


def _run_review_import_results(
    app_config_path: Path,
    profile_path: Path,
    sources_path: Path,
    *,
    json_out: bool,
    export_ui_data: bool,
) -> None:
    app_config, _profile, _sources, session_factory = _load_runtime(app_config_path, profile_path, sources_path)
    with FileLock(_pipeline_lock_path(app_config)):
        with session_factory() as session:
            run = create_pipeline_run(session, "review_import", new_id)
            session.commit()
            try:
                imported = import_review_packets(session, app_config, new_id)
                finish_pipeline_run(run, "success", {"imported": imported})
                session.commit()
                ui_art = _artifacts_for_ui_export(
                    export_ui_data,
                    app_config_path=app_config_path,
                    profile_path=profile_path,
                    sources_path=sources_path,
                )
                _emit_standard_envelope(
                    json_out,
                    "review_import",
                    True,
                    summary={"imported": imported},
                    artifacts={"ui_export": ui_art},
                    text=f"review import complete: imported={imported}",
                )
                if not json_out:
                    _echo_ui_export_status(ui_art, enabled=export_ui_data)
            except typer.Exit:
                raise
            except Exception as exc:
                finish_pipeline_run(run, "failed", error_message=str(exc))
                session.commit()
                _emit_standard_envelope(
                    json_out,
                    "review_import",
                    False,
                    summary={"error": str(exc)},
                    errors=[str(exc)],
                    text=f"review import failed: {exc}",
                )
                raise


@review_app.command("queue")
def review_queue(
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.yaml")),
    sources_path: Path = typer.Option(Path("config/sources.yaml"), "--sources-path", "--sources-dir"),
    limit: int = typer.Option(50, "--limit", min=1, max=500),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Jobs eligible for review export / OpenClaw work (no imported review row yet)."""
    _app_config, profile, _sources, session_factory = _load_runtime(app_config_path, profile_path, sources_path)
    with session_factory() as session:
        rows = fetch_review_queue_rows(session, profile, limit=limit)
    if json_out:
        _emit_standard_envelope(
            json_out,
            "review_queue",
            True,
            summary={"limit": limit, "row_count": len(rows), "rows": rows},
        )
    else:
        for row in rows:
            typer.echo(
                f"{row['job_id']}\tscore={row['score_total']}\t{row['company_name']}\t{row['title']}\tpacket={row['review_packet_status']}"
            )


@review_app.command("export")
def review_export(
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.yaml")),
    sources_path: Path = typer.Option(Path("config/sources.yaml"), "--sources-path", "--sources-dir"),
    export_ui_data: bool = typer.Option(
        False,
        "--export-ui-data/--no-export-ui-data",
        help="Run scripts/export_ui_data.sh after successful export (default: off).",
    ),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    app_config, profile, _sources, session_factory = _load_runtime(app_config_path, profile_path, sources_path)
    with FileLock(_pipeline_lock_path(app_config)):
        with session_factory() as session:
            run = create_pipeline_run(session, "review_export", new_id)
            session.commit()
            try:
                exported = export_review_packets(session, app_config, profile, new_id)
                finish_pipeline_run(run, "success", {"exported": exported})
                session.commit()
                ui_art = _artifacts_for_ui_export(
                    export_ui_data,
                    app_config_path=app_config_path,
                    profile_path=profile_path,
                    sources_path=sources_path,
                )
                _emit_standard_envelope(
                    json_out,
                    "review_export",
                    True,
                    summary={"exported": exported},
                    artifacts={"ui_export": ui_art},
                    text=f"review export complete: exported={exported}",
                )
                if not json_out:
                    _echo_ui_export_status(ui_art, enabled=export_ui_data)
            except typer.Exit:
                raise
            except Exception as exc:
                finish_pipeline_run(run, "failed", error_message=str(exc))
                session.commit()
                _emit_standard_envelope(
                    json_out,
                    "review_export",
                    False,
                    summary={"error": str(exc)},
                    errors=[str(exc)],
                    text=f"review export failed: {exc}",
                )
                raise


@review_app.command("import-results")
def review_import_results(
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.yaml")),
    sources_path: Path = typer.Option(Path("config/sources.yaml"), "--sources-path", "--sources-dir"),
    export_ui_data: bool = typer.Option(
        False,
        "--export-ui-data/--no-export-ui-data",
        help="Run scripts/export_ui_data.sh after successful import (default: off).",
    ),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    _run_review_import_results(
        app_config_path,
        profile_path,
        sources_path,
        json_out=json_out,
        export_ui_data=export_ui_data,
    )


@review_app.command("import")
def review_import_openclaw_results(
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.yaml")),
    sources_path: Path = typer.Option(Path("config/sources.yaml"), "--sources-path", "--sources-dir"),
    export_ui_data: bool = typer.Option(
        False,
        "--export-ui-data/--no-export-ui-data",
        help="Run scripts/export_ui_data.sh after successful import (default: off).",
    ),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Same as import-results (reads review inbox → SQLite)."""
    _run_review_import_results(
        app_config_path,
        profile_path,
        sources_path,
        json_out=json_out,
        export_ui_data=export_ui_data,
    )


@digest_app.command("send")
def digest_send(
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.yaml")),
    sources_path: Path = typer.Option(Path("config/sources.yaml"), "--sources-path", "--sources-dir"),
    digest_date: str | None = typer.Option(None),
    dry_run: bool = typer.Option(False, help="Build digest without sending email"),
    export_ui_data: bool = typer.Option(
        False,
        "--export-ui-data/--no-export-ui-data",
        help="Run scripts/export_ui_data.sh after successful digest send (default: off).",
    ),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    app_config, profile, _sources, session_factory = _load_runtime(app_config_path, profile_path, sources_path)
    with FileLock(_pipeline_lock_path(app_config)):
        with session_factory() as session:
            run = create_pipeline_run(session, "digest_send", new_id)
            session.commit()
            try:
                digest = send_digest(session, app_config, profile, id_factory=new_id, digest_date=digest_date, dry_run=dry_run)
                finish_pipeline_run(run, "success", {"digest_id": digest.id, "status": digest.status, "dry_run": dry_run})
                session.commit()
                ui_art = _artifacts_for_ui_export(
                    export_ui_data,
                    app_config_path=app_config_path,
                    profile_path=profile_path,
                    sources_path=sources_path,
                )
                summary: dict[str, Any] = {
                    "dry_run": dry_run,
                    "digest_id": digest.id,
                    "digest_status": digest.status,
                }
                if dry_run:
                    summary["body_text"] = digest.body_text
                _emit_standard_envelope(
                    json_out,
                    "digest_send",
                    True,
                    summary=summary,
                    artifacts={"ui_export": ui_art},
                    text=(
                        f"digest dry-run complete: digest_id={digest.id} items={len(digest.body_text.splitlines())}"
                        if dry_run
                        else f"digest send complete: digest_id={digest.id} status={digest.status}"
                    ),
                )
                if not json_out and dry_run:
                    typer.echo(digest.body_text)
                if not json_out:
                    _echo_ui_export_status(ui_art, enabled=export_ui_data)
            except Exception as exc:
                finish_pipeline_run(run, "failed", error_message=str(exc))
                session.commit()
                _emit_standard_envelope(
                    json_out,
                    "digest_send",
                    False,
                    summary={"error": str(exc)},
                    errors=[str(exc)],
                    text=f"digest send failed: {exc}",
                )
                raise typer.Exit(code=1)


@digest_app.command("resend")
def digest_resend(
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.yaml")),
    sources_path: Path = typer.Option(Path("config/sources.yaml"), "--sources-path", "--sources-dir"),
    digest_date: str = typer.Option(...),
    dry_run: bool = typer.Option(False, help="Build digest without sending email"),
    export_ui_data: bool = typer.Option(
        False,
        "--export-ui-data/--no-export-ui-data",
        help="Run scripts/export_ui_data.sh after successful digest resend (default: off).",
    ),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    app_config, profile, _sources, session_factory = _load_runtime(app_config_path, profile_path, sources_path)
    with FileLock(_pipeline_lock_path(app_config)):
        with session_factory() as session:
            original = session.scalar(select(Digest).where(Digest.digest_date == digest_date).order_by(Digest.sent_at.desc()))
            if original is None:
                msg = f"no_digest_for_date:{digest_date}"
                _emit_standard_envelope(
                    json_out,
                    "digest_resend",
                    False,
                    summary={"error_code": "no_digest_for_date", "digest_date": digest_date},
                    errors=[msg],
                    text=f"digest resend failed: no digest for {digest_date}",
                )
                raise typer.Exit(code=1)
            run = create_pipeline_run(session, "digest_resend", new_id)
            session.commit()
            try:
                digest = send_digest(
                    session,
                    app_config,
                    profile,
                    id_factory=new_id,
                    digest_date=digest_date,
                    resend_of_digest_id=original.id,
                    dry_run=dry_run,
                )
                finish_pipeline_run(run, "success", {"digest_id": digest.id, "status": digest.status, "resend_of": original.id, "dry_run": dry_run})
                session.commit()
                ui_art = _artifacts_for_ui_export(
                    export_ui_data,
                    app_config_path=app_config_path,
                    profile_path=profile_path,
                    sources_path=sources_path,
                )
                summary = {
                    "dry_run": dry_run,
                    "digest_id": digest.id,
                    "digest_status": digest.status,
                    "resend_of": original.id,
                }
                if dry_run:
                    summary["body_text"] = digest.body_text
                _emit_standard_envelope(
                    json_out,
                    "digest_resend",
                    True,
                    summary=summary,
                    artifacts={"ui_export": ui_art},
                    text=(
                        f"digest resend dry-run complete: digest_id={digest.id}"
                        if dry_run
                        else f"digest resend complete: digest_id={digest.id} status={digest.status}"
                    ),
                )
                if not json_out and dry_run:
                    typer.echo(digest.body_text)
                if not json_out:
                    _echo_ui_export_status(ui_art, enabled=export_ui_data)
            except Exception as exc:
                finish_pipeline_run(run, "failed", error_message=str(exc))
                session.commit()
                _emit_standard_envelope(
                    json_out,
                    "digest_resend",
                    False,
                    summary={"error": str(exc)},
                    errors=[str(exc)],
                    text=f"digest resend failed: {exc}",
                )
                raise typer.Exit(code=1)


@feedback_app.command("record")
def feedback_record(
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.yaml")),
    sources_path: Path = typer.Option(Path("config/sources.yaml"), "--sources-path", "--sources-dir"),
    feedback_type: str = typer.Option(...),
    cluster_id: str | None = typer.Option(None),
    company_name: str | None = typer.Option(None),
    title_keyword: str | None = typer.Option(None),
    notes: str | None = typer.Option(None),
) -> None:
    if feedback_type not in ALLOWED_FEEDBACK_TYPES:
        typer.echo(f"feedback failed: invalid feedback type {feedback_type}")
        raise typer.Exit(code=1)
    app_config, _profile, _sources, session_factory = _load_runtime(app_config_path, profile_path, sources_path)
    with FileLock(_pipeline_lock_path(app_config)):
        with session_factory() as session:
            record = record_feedback(
                session,
                id_factory=new_id,
                feedback_type=feedback_type,
                cluster_id=cluster_id,
                company_name=company_name,
                title_keyword=title_keyword,
                notes=notes,
            )
            session.commit()
            typer.echo(f"feedback recorded: id={record.id} type={record.feedback_type}")


@submissions_app.command("list")
def submissions_list(
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.yaml")),
    sources_path: Path = typer.Option(Path("config/sources.yaml"), "--sources-path", "--sources-dir"),
    status: list[str] = typer.Option([], "--status"),
    limit: int = typer.Option(50, "--limit", min=1, max=500),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    app_config, _profile, _sources, session_factory = _load_runtime(app_config_path, profile_path, sources_path)
    with session_factory() as session:
        stmt = select(ApplicationSubmission).order_by(ApplicationSubmission.created_at.desc()).limit(limit)
        if status:
            stmt = stmt.where(ApplicationSubmission.status.in_(status))
        rows = session.scalars(stmt).all()
    payload = {
        "command": "submissions_list",
        "status": "ok",
        "count": len(rows),
        "items": [
            {
                "id": row.id,
                "job_id": row.job_id,
                "cluster_id": row.cluster_id,
                "status": row.status,
                "channel": row.channel,
                "submitted_at": row.submitted_at.isoformat() if row.submitted_at else None,
                "external_ref": row.external_ref,
                "notes": row.notes,
                "created_at": row.created_at.isoformat(),
                "updated_at": row.updated_at.isoformat(),
            }
            for row in rows
        ],
    }
    if json_out:
        typer.echo(json.dumps(payload, indent=2))
        return
    if not rows:
        typer.echo("submissions list: no records")
        return
    for row in payload["items"]:
        typer.echo(
            f"{row['id']} status={row['status']} channel={row['channel']} "
            f"job_id={row['job_id']} submitted_at={row['submitted_at'] or '-'}"
        )


@submissions_app.command("record")
def submissions_record(
    job_id: str = typer.Option(..., "--job-id"),
    status: str = typer.Option(..., "--status"),
    channel: str = typer.Option(..., "--channel"),
    external_ref: str | None = typer.Option(None, "--external-ref"),
    notes: str | None = typer.Option(None, "--notes"),
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.yaml")),
    sources_path: Path = typer.Option(Path("config/sources.yaml"), "--sources-path", "--sources-dir"),
) -> None:
    if status not in SUBMISSION_STATUSES:
        typer.echo(f"submissions record failed: invalid_status:{status}")
        raise typer.Exit(code=1)
    app_config, _profile, _sources, session_factory = _load_runtime(app_config_path, profile_path, sources_path)
    with FileLock(_pipeline_lock_path(app_config)):
        with session_factory() as session:
            try:
                cluster_id = _cluster_id_for_job(session, job_id)
            except ValueError as exc:
                typer.echo(f"submissions record failed: {exc}")
                raise typer.Exit(code=1)
            submitted_at = utcnow() if status in {"submitted", "interview", "rejected", "offer", "withdrawn"} else None
            record = create_application_submission(
                session,
                id_factory=new_id,
                job_id=job_id,
                cluster_id=cluster_id,
                status=status,
                channel=channel,
                submitted_at=submitted_at,
                external_ref=external_ref,
                notes=notes,
            )
            _record_submission_feedback(session, cluster_id=cluster_id, status=status, notes=notes)
            session.commit()
            typer.echo(f"submissions record: id={record.id} status={record.status} job_id={record.job_id}")


@submissions_app.command("update")
def submissions_update(
    id: str = typer.Option(..., "--id"),
    status: str = typer.Option(..., "--status"),
    external_ref: str | None = typer.Option(None, "--external-ref"),
    notes: str | None = typer.Option(None, "--notes"),
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.yaml")),
    sources_path: Path = typer.Option(Path("config/sources.yaml"), "--sources-path", "--sources-dir"),
) -> None:
    if status not in SUBMISSION_STATUSES:
        typer.echo(f"submissions update failed: invalid_status:{status}")
        raise typer.Exit(code=1)
    app_config, _profile, _sources, session_factory = _load_runtime(app_config_path, profile_path, sources_path)
    with FileLock(_pipeline_lock_path(app_config)):
        with session_factory() as session:
            record = session.get(ApplicationSubmission, id)
            if record is None:
                typer.echo(f"submissions update failed: submission_not_found:{id}")
                raise typer.Exit(code=1)
            submitted_at = record.submitted_at
            if status in {"submitted", "interview", "rejected", "offer", "withdrawn"} and submitted_at is None:
                submitted_at = utcnow()
            update_application_submission(
                record,
                status=status,
                submitted_at=submitted_at,
                external_ref=external_ref,
                notes=notes,
            )
            _record_submission_feedback(session, cluster_id=record.cluster_id, status=status, notes=notes)
            session.commit()
            typer.echo(f"submissions update: id={record.id} status={record.status}")


def _cluster_id_for_job(session, job_id: str) -> str:
    cluster_id = session.scalar(
        select(JobClusterMember.cluster_id).where(JobClusterMember.normalized_job_id == job_id).limit(1)
    )
    if cluster_id is None:
        raise ValueError(f"job_cluster_not_found:{job_id}")
    return cluster_id


def _record_submission_feedback(session, *, cluster_id: str, status: str, notes: str | None = None) -> None:
    feedback_type = SUBMISSION_FEEDBACK_MAP.get(status)
    if feedback_type is None:
        return
    create_job_feedback(
        session,
        id_factory=new_id,
        feedback_type=feedback_type,
        cluster_id=cluster_id,
        notes=notes,
    )


@app.command()
def rerank(
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.yaml")),
    sources_path: Path = typer.Option(Path("config/sources.yaml"), "--sources-path", "--sources-dir"),
) -> None:
    rank(app_config_path=app_config_path, profile_path=profile_path, sources_path=sources_path)


@reprocess_app.command("review-packets")
def reprocess_review_packets(
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.yaml")),
    sources_path: Path = typer.Option(Path("config/sources.yaml"), "--sources-path", "--sources-dir"),
) -> None:
    review_export(app_config_path=app_config_path, profile_path=profile_path, sources_path=sources_path)


@reprocess_app.command("normalize")
def reprocess_normalize(
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.yaml")),
    sources_path: Path = typer.Option(Path("config/sources.yaml"), "--sources-path", "--sources-dir"),
    source_job_id: str = typer.Option(...),
) -> None:
    app_config, _profile, _sources, session_factory = _load_runtime(app_config_path, profile_path, sources_path)
    with FileLock(_pipeline_lock_path(app_config)):
        with session_factory() as session:
            run = create_pipeline_run(session, "reprocess_normalize", new_id)
            session.commit()
            try:
                row = session.execute(
                    select(SourceJob, Source, NormalizedJob)
                    .join(Source, Source.id == SourceJob.source_id)
                    .join(NormalizedJob, NormalizedJob.source_job_id == SourceJob.id)
                    .where(SourceJob.id == source_job_id)
                ).first()
                if row is None:
                    raise ValueError(f"source_job_not_found:{source_job_id}")
                source_job, source, normalized = row
                record = _record_from_existing(source_job, source, normalized)
                canonical = normalize_job(
                    source_job.id,
                    source.id,
                    source_job.seen_at,
                    record,
                    source_name=source.name,
                    source_kind=source.kind,
                    source_priority=source.priority,
                    source_trust_weight=source.trust_weight,
                )
                from findmejobs.db.repositories import upsert_normalized_job
                from findmejobs.dedupe.clustering import assign_job_cluster

                normalized_row, _created = upsert_normalized_job(session, canonical, new_id)
                assign_job_cluster(session, normalized_row, new_id)
                finish_pipeline_run(run, "success", {"source_job_id": source_job.id})
                session.commit()
                typer.echo(f"reprocess normalize complete: source_job_id={source_job.id}")
            except Exception as exc:
                finish_pipeline_run(run, "failed", error_message=str(exc))
                session.commit()
                typer.echo(f"reprocess normalize failed: {exc}")
                raise typer.Exit(code=1)


@app.command()
def report(
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.yaml")),
    sources_path: Path = typer.Option(Path("config/sources.yaml"), "--sources-path", "--sources-dir"),
) -> None:
    app_config, _profile, _sources, session_factory = _load_runtime(app_config_path, profile_path, sources_path)
    with session_factory() as session:
        report_payload = build_report(session, quality=app_config.quality)
    typer.echo(json.dumps(report_payload, indent=2))


@jobs_app.command("list")
def jobs_list(
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.yaml")),
    sources_path: Path = typer.Option(Path("config/sources.yaml"), "--sources-path", "--sources-dir"),
    all_scored: bool = typer.Option(
        False,
        "--all-scored",
        help="Include hard-filtered and below-minimum-score rows (still for the current profile/rank model).",
    ),
    limit: int = typer.Option(50, "--limit", min=1, max=500),
    snippet_length: int = typer.Option(160, "--snippet-length", min=20, max=2000),
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Emit JSON (same row set as plain text). Use with --all-scored to include hard-filtered / below-minimum rows.",
    ),
) -> None:
    """Print a short preview of ranked jobs (title, score, tags, signals, description snippet).

    Default rows match review export eligibility (passed hard filters, score ≥ ranking.minimum_score).
    Run `findmejobs rank` first so scores exist for the current profile.
    """
    _app_config, profile, _sources, session_factory = _load_runtime(app_config_path, profile_path, sources_path)
    with session_factory() as session:
        previews = fetch_job_previews(
            session,
            profile,
            all_scored=all_scored,
            limit=limit,
            snippet_length=snippet_length,
        )
    if json_out:
        summary = {
            "filter": "all_scored" if all_scored else "review_eligible",
            "hint": (
                None
                if all_scored or previews
                else "Only passed hard filters and score ≥ ranking.minimum_score. "
                "Pass --all-scored to include hard_filtered and below_threshold rows in JSON/text."
            ),
            "limit": limit,
            "jobs": [p.to_json_dict() for p in previews],
        }
        _emit_standard_envelope(json_out, "jobs_list", True, summary=summary)
    else:
        typer.echo(format_job_previews_text(previews), nl=False)


@jobs_app.command("top")
def jobs_top(
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.yaml")),
    sources_path: Path = typer.Option(Path("config/sources.yaml"), "--sources-path", "--sources-dir"),
    limit: int = typer.Option(20, "--limit", min=1, max=500),
    snippet_length: int = typer.Option(200, "--snippet-length", min=20, max=2000),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Top-ranked jobs for quick operator/OpenClaw inspection (review-eligible rows by default)."""
    _app_config, profile, _sources, session_factory = _load_runtime(app_config_path, profile_path, sources_path)
    with session_factory() as session:
        previews = fetch_job_previews(
            session,
            profile,
            all_scored=False,
            limit=limit,
            snippet_length=snippet_length,
        )
    if json_out:
        summary = {
            "filter": "review_eligible",
            "limit": limit,
            "jobs": [p.to_json_dict() for p in previews],
        }
        _emit_standard_envelope(json_out, "jobs_top", True, summary=summary)
    else:
        typer.echo(format_job_previews_text(previews), nl=False)


@ranking_app.command("explain")
def ranking_explain(
    profile_path: Path = typer.Option(Path("config/profile.yaml")),
    json_out: bool = typer.Option(False, "--json", help="Emit structured JSON (includes effective policy + catalogs)."),
) -> None:
    """Show how hard filters and score components map to config; dump effective ranking policy."""
    try:
        resolved = resolve_profile_config_path(profile_path)
    except FileNotFoundError as exc:
        typer.echo(f"ranking explain failed: {exc}")
        raise typer.Exit(code=1)
    ranking_path = resolved.with_name("ranking.yaml")
    if not ranking_path.exists():
        typer.echo(f"ranking explain failed: missing {ranking_path}")
        raise typer.Exit(code=1)
    try:
        profile = load_profile_config(profile_path)
    except (FileNotFoundError, ValidationError, ValueError) as exc:
        typer.echo(f"ranking explain failed: {exc}")
        raise typer.Exit(code=1)
    payload = build_ranking_explain_payload(
        profile,
        profile_path=str(resolved),
        ranking_path=str(ranking_path),
    )
    if json_out:
        _emit_standard_envelope(json_out, "ranking_explain", True, summary=payload)
    else:
        typer.echo(format_ranking_explain_text(payload), nl=False)


@ranking_app.command("show")
def ranking_show(
    profile_path: Path = typer.Option(Path("config/profile.yaml")),
    json_out: bool = typer.Option(False, "--json", help="Emit canonical ranking.yaml as validated structured JSON."),
) -> None:
    try:
        resolved = resolve_profile_config_path(profile_path)
    except FileNotFoundError as exc:
        typer.echo(f"ranking show failed: {exc}")
        raise typer.Exit(code=1)
    ranking_path = resolved.with_name("ranking.yaml")
    ranking = load_existing_ranking(ranking_path)
    if ranking is None:
        typer.echo(f"ranking show failed: missing {ranking_path}")
        raise typer.Exit(code=1)
    if json_out:
        _emit_standard_envelope(
            json_out,
            "ranking_show",
            True,
            summary={"ranking": ranking.model_dump(mode="json")},
            artifacts={"ranking_path": str(ranking_path.resolve())},
        )
    else:
        typer.echo(json.dumps(ranking.model_dump(mode="json"), indent=2))


@ranking_app.command("set")
def ranking_set(
    profile_path: Path = typer.Option(Path("config/profile.yaml")),
    stale_days: int | None = typer.Option(None, min=1, help="ranking.stale_days"),
    minimum_score: float | None = typer.Option(None, help="Minimum total score for review export eligibility."),
    minimum_salary: int | None = typer.Option(None, min=0, help="Salary floor (job.salary_max must clear it when present)."),
    clear_minimum_salary: bool = typer.Option(False, help="Set minimum_salary to null in ranking.yaml."),
    rank_model_version: str | None = typer.Option(None, help="Bump when you change weights/rules and need fresh scores."),
    require_remote: bool | None = typer.Option(
        None,
        "--require-remote/--no-require-remote",
        help="Hard-filter non-remote jobs when --require-remote; omit both flags to leave unchanged.",
    ),
    remote_first: bool | None = typer.Option(
        None,
        "--remote-first/--no-remote-first",
        help="Soft signal (weights); omit both flags to leave unchanged.",
    ),
    add_blocked_company: list[str] = typer.Option([], "--add-blocked-company"),
    remove_blocked_company: list[str] = typer.Option([], "--remove-blocked-company"),
    add_blocked_title_keyword: list[str] = typer.Option([], "--add-blocked-title-keyword"),
    remove_blocked_title_keyword: list[str] = typer.Option([], "--remove-blocked-title-keyword"),
    add_allowed_company: list[str] = typer.Option([], "--add-allowed-company"),
    remove_allowed_company: list[str] = typer.Option([], "--remove-allowed-company"),
    add_preferred_company: list[str] = typer.Option([], "--add-preferred-company"),
    remove_preferred_company: list[str] = typer.Option([], "--remove-preferred-company"),
    add_preferred_timezone: list[str] = typer.Option([], "--add-preferred-timezone"),
    remove_preferred_timezone: list[str] = typer.Option([], "--remove-preferred-timezone"),
    set_weight: list[str] = typer.Option([], "--set-weight", help="Repeatable NAME=VALUE (e.g. title_alignment=25)"),
    title_family_add: list[str] = typer.Option([], "--title-family-add", help="Repeatable FAMILY:PATTERN"),
    title_family_remove: list[str] = typer.Option([], "--title-family-remove", help="Repeatable FAMILY:PATTERN"),
    title_family_clear: list[str] = typer.Option([], "--title-family-clear", help="Repeatable FAMILY"),
) -> None:
    """Patch ranking.yaml with validated scalar/list/weights/title-family updates."""
    resolved = resolve_profile_config_path(profile_path)
    ranking_path = resolved.with_name("ranking.yaml")
    if not ranking_path.exists():
        typer.echo(f"ranking set failed: missing {ranking_path}")
        raise typer.Exit(code=1)
    if clear_minimum_salary and minimum_salary is not None:
        typer.echo("ranking set failed: use either --clear-minimum-salary or --minimum-salary, not both")
        raise typer.Exit(code=1)
    any_patch = (
        stale_days is not None
        or minimum_score is not None
        or minimum_salary is not None
        or clear_minimum_salary
        or rank_model_version is not None
        or require_remote is not None
        or remote_first is not None
        or bool(add_blocked_company)
        or bool(remove_blocked_company)
        or bool(add_blocked_title_keyword)
        or bool(remove_blocked_title_keyword)
        or bool(add_allowed_company)
        or bool(remove_allowed_company)
        or bool(add_preferred_company)
        or bool(remove_preferred_company)
        or bool(add_preferred_timezone)
        or bool(remove_preferred_timezone)
        or bool(set_weight)
        or bool(title_family_add)
        or bool(title_family_remove)
        or bool(title_family_clear)
    )
    if not any_patch:
        typer.echo("ranking set: pass at least one option (see findmejobs ranking set --help)")
        raise typer.Exit(code=1)
    try:
        draft = patch_ranking_yaml(
            ranking_path,
            stale_days=stale_days,
            minimum_score=minimum_score,
            minimum_salary=minimum_salary,
            clear_minimum_salary=clear_minimum_salary,
            rank_model_version=rank_model_version,
            require_remote=require_remote,
            remote_first=remote_first,
        )
        payload = draft.model_dump(mode="python")
        payload["blocked_companies"] = _set_list_with_ops(payload.get("blocked_companies"), add_blocked_company, remove_blocked_company)
        payload["blocked_title_keywords"] = _set_list_with_ops(
            payload.get("blocked_title_keywords"),
            add_blocked_title_keyword,
            remove_blocked_title_keyword,
        )
        payload["allowed_companies"] = _set_list_with_ops(payload.get("allowed_companies"), add_allowed_company, remove_allowed_company)
        payload["preferred_companies"] = _set_list_with_ops(payload.get("preferred_companies"), add_preferred_company, remove_preferred_company)
        payload["preferred_timezones"] = _set_list_with_ops(
            payload.get("preferred_timezones"),
            add_preferred_timezone,
            remove_preferred_timezone,
        )
        weights = dict(payload.get("weights", {}))
        for item in set_weight:
            if "=" not in item:
                raise ValueError(f"invalid_weight:{item}")
            key, raw_value = item.split("=", 1)
            key = key.strip()
            if key not in RankingWeights.model_fields:
                raise ValueError(f"unknown_weight:{key}")
            weights[key] = float(raw_value)
        payload["weights"] = weights
        families: dict[str, list[str]] = dict(payload.get("title_families") or {})
        for item in title_family_add:
            family, pattern = _split_family_pattern(item, kind="title_family_add")
            families[family] = _dedupe_str_list([*(families.get(family) or []), pattern])
        for item in title_family_remove:
            family, pattern = _split_family_pattern(item, kind="title_family_remove")
            existing = [value for value in families.get(family, []) if value.casefold() != pattern.casefold()]
            if existing:
                families[family] = existing
            elif family in families:
                families.pop(family)
        for family in title_family_clear:
            families.pop(family, None)
        payload["title_families"] = families
        validated = RankingConfigDraft.model_validate(payload)
        dump_yaml(validated.model_dump(mode="json"), ranking_path)
    except ValidationError as exc:
        typer.echo(f"ranking set failed: {exc}")
        raise typer.Exit(code=1)
    except ValueError as exc:
        typer.echo(f"ranking set failed: {exc}")
        raise typer.Exit(code=1)
    typer.echo(f"ranking set: wrote {ranking_path} (run `findmejobs rank` to refresh scores)")


@ranking_app.command("audit")
def ranking_audit(
    fixture: str = typer.Option(..., "--fixture", help="Fixture name (config/examples/ranking_audit/<name>.json) or explicit path."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    try:
        fixture_path = resolve_ranking_audit_fixture(fixture)
        result = run_ranking_audit(fixture_path)
    except Exception as exc:  # noqa: BLE001
        payload = {"command": "ranking_audit", "status": "failed", "error": str(exc)}
        _emit_json(json_out, payload, f"ranking audit failed: {exc}")
        raise typer.Exit(code=1)
    payload = {
        "command": "ranking_audit",
        "status": "ok" if result.passed else "failed",
        "fixture_path": str(result.fixture_path),
        "errors": result.errors,
        "actual_ordered_job_ids": result.actual_ordered_job_ids,
        "actual_scores": result.actual_scores,
        "actual_top_reasons": result.actual_top_reasons,
    }
    if result.passed:
        _emit_json(json_out, payload, f"ranking audit passed: fixture={result.fixture_path}")
        return
    _emit_json(json_out, payload, f"ranking audit failed: {result.errors}")
    raise typer.Exit(code=1)


@app.command()
def doctor(
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.yaml")),
    sources_path: Path = typer.Option(Path("config/sources.yaml"), "--sources-path", "--sources-dir"),
    strict: bool = typer.Option(False, "--strict", help="Fail when quality gates exceed configured [quality] thresholds."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    try:
        app_config, _profile, _sources, session_factory = _load_runtime(app_config_path, profile_path, sources_path)
    except (FileNotFoundError, ValidationError, ValueError) as exc:
        errs = [f"invalid_profile_config:{exc}"]
        _emit_standard_envelope(
            json_out,
            "doctor",
            False,
            summary={"strict": strict, "hints": {}},
            errors=errs,
            text=f"doctor failed: {errs}",
        )
        raise typer.Exit(code=1)
    with session_factory() as session:
        errors = run_doctor(
            session,
            app_config.database.url,
            [
                app_config.storage.root_dir,
                app_config.storage.raw_dir,
                app_config.storage.review_outbox_dir,
                app_config.storage.review_inbox_dir,
                app_config.storage.lock_dir,
            ],
        )
        if strict:
            errors.extend(quality_gate_failures(session, app_config.quality))
    errors.extend(check_profile_config_health(profile_path.parent))
    if errors:
        hints = doctor_failure_hints(errors)
        _emit_standard_envelope(
            json_out,
            "doctor",
            False,
            summary={"strict": strict, "hints": hints},
            errors=errors,
            text=None,
        )
        if not json_out:
            typer.echo(f"doctor failed: {errors}")
            if hints:
                typer.echo("")
                typer.echo("Why / what to do:")
                for code, text in hints.items():
                    typer.echo(f"  • {code}: {text}")
        raise typer.Exit(code=1)
    _emit_standard_envelope(json_out, "doctor", True, summary={"strict": strict, "hints": {}}, text="doctor ok")


def _profile_service(state_root: Path, config_root: Path) -> ProfileBootstrapService:
    return ProfileBootstrapService(state_root=state_root, config_root=config_root, id_factory=new_id)


def _application_service(state_root: Path) -> ApplicationDraftService:
    return ApplicationDraftService(state_root=state_root)


def _apply_service(application_state_root: Path, apply_state_root: Path) -> ApplySessionService:
    return ApplySessionService(application_state_root=application_state_root, apply_state_root=apply_state_root)


@app.command("prepare-application")
def prepare_application(
    job_id: str = typer.Option(...),
    questions_file: Path | None = typer.Option(None, exists=True, dir_okay=False),
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.yaml")),
    sources_path: Path = typer.Option(Path("config/sources.yaml"), "--sources-path", "--sources-dir"),
    state_root: Path = typer.Option(Path("state/applications")),
    export_ui_data: bool = typer.Option(
        False,
        "--export-ui-data/--no-export-ui-data",
        help="Run scripts/export_ui_data.sh after successful prepare-application (default: off).",
    ),
) -> None:
    app_config, profile, _sources, session_factory = _load_runtime(app_config_path, profile_path, sources_path)
    service = _application_service(state_root)
    with FileLock(_pipeline_lock_path(app_config)):
        with session_factory() as session:
            try:
                packet, missing_inputs = service.prepare_application(
                    session,
                    profile,
                    job_id=job_id,
                    questions_file=questions_file,
                )
            except (FileNotFoundError, ValueError) as exc:
                typer.echo(f"prepare-application failed: {exc}")
                raise typer.Exit(code=1)
    readiness_state, blockers, _categories = service.readiness_from_packet(packet=packet, missing_inputs=missing_inputs)
    typer.echo(
        f"prepare-application complete: job_id={packet.job_id} "
        f"questions={len(packet.application_questions)} missing_inputs={len(missing_inputs)} "
        f"readiness={readiness_state}"
    )
    if blockers:
        typer.echo("readiness_blockers: " + ", ".join(blockers))
    ui_art = _artifacts_for_ui_export(
        export_ui_data,
        app_config_path=app_config_path,
        profile_path=profile_path,
        sources_path=sources_path,
    )
    _echo_ui_export_status(ui_art, enabled=export_ui_data)


@app.command("draft-cover-letter")
def draft_cover_letter(
    job_id: str = typer.Option(...),
    questions_file: Path | None = typer.Option(None, exists=True, dir_okay=False),
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.yaml")),
    sources_path: Path = typer.Option(Path("config/sources.yaml"), "--sources-path", "--sources-dir"),
    state_root: Path = typer.Option(Path("state/applications")),
    export_ui_data: bool = typer.Option(
        False,
        "--export-ui-data/--no-export-ui-data",
        help="Run scripts/export_ui_data.sh after successful draft-cover-letter (default: off).",
    ),
) -> None:
    app_config, profile, _sources, session_factory = _load_runtime(app_config_path, profile_path, sources_path)
    service = _application_service(state_root)
    with FileLock(_pipeline_lock_path(app_config)):
        with session_factory() as session:
            try:
                draft = service.draft_cover_letter(
                    session,
                    profile,
                    job_id=job_id,
                    questions_file=questions_file,
                )
            except (FileNotFoundError, ValueError) as exc:
                typer.echo(f"draft-cover-letter failed: {exc}")
                raise typer.Exit(code=1)
    typer.echo(f"draft-cover-letter complete: job_id={draft.job_id} origin={draft.origin}")
    ui_art = _artifacts_for_ui_export(
        export_ui_data,
        app_config_path=app_config_path,
        profile_path=profile_path,
        sources_path=sources_path,
    )
    _echo_ui_export_status(ui_art, enabled=export_ui_data)


@app.command("draft-answers")
def draft_answers(
    job_id: str = typer.Option(...),
    questions_file: Path | None = typer.Option(None, exists=True, dir_okay=False),
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.yaml")),
    sources_path: Path = typer.Option(Path("config/sources.yaml"), "--sources-path", "--sources-dir"),
    state_root: Path = typer.Option(Path("state/applications")),
    export_ui_data: bool = typer.Option(
        False,
        "--export-ui-data/--no-export-ui-data",
        help="Run scripts/export_ui_data.sh after successful draft-answers (default: off).",
    ),
) -> None:
    app_config, profile, _sources, session_factory = _load_runtime(app_config_path, profile_path, sources_path)
    service = _application_service(state_root)
    with FileLock(_pipeline_lock_path(app_config)):
        with session_factory() as session:
            try:
                draft = service.draft_answers(
                    session,
                    profile,
                    job_id=job_id,
                    questions_file=questions_file,
                )
            except (FileNotFoundError, ValueError) as exc:
                typer.echo(f"draft-answers failed: {exc}")
                raise typer.Exit(code=1)
    typer.echo(f"draft-answers complete: job_id={draft.job_id} answers={len(draft.answers)} origin={draft.origin}")
    ui_art = _artifacts_for_ui_export(
        export_ui_data,
        app_config_path=app_config_path,
        profile_path=profile_path,
        sources_path=sources_path,
    )
    _echo_ui_export_status(ui_art, enabled=export_ui_data)


@app.command("show-application")
def show_application(
    job_id: str = typer.Option(...),
    state_root: Path = typer.Option(Path("state/applications")),
) -> None:
    service = _application_service(state_root)
    try:
        payload = service.show_application(job_id=job_id)
    except FileNotFoundError as exc:
        typer.echo(f"show-application failed: {exc}")
        raise typer.Exit(code=1)
    typer.echo(json.dumps(payload, indent=2, default=str))


@applications_app.command("queue")
def applications_queue(
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.yaml")),
    sources_path: Path = typer.Option(Path("config/sources.yaml"), "--sources-path", "--sources-dir"),
    state_root: Path = typer.Option(Path("state/applications")),
    limit: int = typer.Option(50, "--limit", min=1, max=500),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Ranked jobs that still need application packet, drafts, or OpenClaw follow-up."""
    _app_config, profile, _sources, session_factory = _load_runtime(app_config_path, profile_path, sources_path)
    with session_factory() as session:
        rows = fetch_applications_queue_rows(session, profile, state_root, limit=limit)
    if json_out:
        _emit_standard_envelope(
            json_out,
            "applications_queue",
            True,
            summary={"limit": limit, "row_count": len(rows), "rows": rows},
            artifacts={"applications_state_root": str(state_root.resolve())},
        )
    else:
        for row in rows:
            typer.echo(
                f"{row['job_id']}\t{row['reason']}\tscore={row['score_total']}\t{row['company_name']}\t{row['title']}"
            )


@app.command("validate-application")
def validate_application(
    job_id: str = typer.Option(...),
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.yaml")),
    sources_path: Path = typer.Option(Path("config/sources.yaml"), "--sources-path", "--sources-dir"),
    state_root: Path = typer.Option(Path("state/applications")),
) -> None:
    app_config, profile, _sources, session_factory = _load_runtime(app_config_path, profile_path, sources_path)
    service = _application_service(state_root)
    with session_factory() as session:
        report = service.validate_application(session, profile, job_id=job_id)
    typer.echo(json.dumps(report.model_dump(mode="json"), indent=2))
    if report.errors:
        raise typer.Exit(code=1)


@app.command("regenerate-application")
def regenerate_application(
    job_id: str = typer.Option(...),
    questions_file: Path | None = typer.Option(None, exists=True, dir_okay=False),
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.yaml")),
    sources_path: Path = typer.Option(Path("config/sources.yaml"), "--sources-path", "--sources-dir"),
    state_root: Path = typer.Option(Path("state/applications")),
    export_ui_data: bool = typer.Option(
        False,
        "--export-ui-data/--no-export-ui-data",
        help="Run scripts/export_ui_data.sh after successful regenerate-application (default: off).",
    ),
) -> None:
    app_config, profile, _sources, session_factory = _load_runtime(app_config_path, profile_path, sources_path)
    service = _application_service(state_root)
    with FileLock(_pipeline_lock_path(app_config)):
        with session_factory() as session:
            try:
                result = service.regenerate_application(
                    session,
                    profile,
                    job_id=job_id,
                    questions_file=questions_file,
                )
            except (FileNotFoundError, ValueError) as exc:
                typer.echo(f"regenerate-application failed: {exc}")
                raise typer.Exit(code=1)
    typer.echo(json.dumps(result, indent=2))
    ui_art = _artifacts_for_ui_export(
        export_ui_data,
        app_config_path=app_config_path,
        profile_path=profile_path,
        sources_path=sources_path,
    )
    _echo_ui_export_status(ui_art, enabled=export_ui_data)


@app.command("draft-applications")
def draft_applications(
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.yaml")),
    sources_path: Path = typer.Option(Path("config/sources.yaml"), "--sources-path", "--sources-dir"),
    state_root: Path = typer.Option(Path("state/applications")),
    questions_file: Path | None = typer.Option(None, exists=True, dir_okay=False),
    limit: int = typer.Option(100, "--limit", min=1, max=1000, help="Maximum review-eligible ranked jobs to process."),
    fail_fast: bool = typer.Option(False, "--fail-fast", help="Stop on first per-job failure."),
    json_out: bool = typer.Option(False, "--json", help="Emit machine-readable batch output."),
    export_ui_data: bool = typer.Option(
        False,
        "--export-ui-data/--no-export-ui-data",
        help="Run scripts/export_ui_data.sh after successful draft-applications (default: off).",
    ),
) -> None:
    app_config, profile, _sources, session_factory = _load_runtime(app_config_path, profile_path, sources_path)
    service = _application_service(state_root)
    results: list[dict[str, object]] = []
    failures: list[dict[str, str]] = []
    selected_jobs = 0
    with FileLock(_pipeline_lock_path(app_config)):
        with session_factory() as session:
            previews = fetch_job_previews(
                session,
                profile,
                all_scored=False,
                limit=limit,
                snippet_length=120,
            )
            selected_jobs = len(previews)
            for preview in previews:
                try:
                    result = service.regenerate_application(
                        session,
                        profile,
                        job_id=preview.job_id,
                        questions_file=questions_file,
                    )
                    missing_inputs = result.get("missing_inputs", [])
                    readiness = "needs_input" if missing_inputs else "ready"
                    results.append(
                        {
                            "job_id": preview.job_id,
                            "cover_letter_origin": result.get("cover_letter_origin"),
                            "answers_origin": result.get("answers_origin"),
                            "missing_inputs": len(missing_inputs) if isinstance(missing_inputs, list) else 0,
                            "readiness": readiness,
                        }
                    )
                except (FileNotFoundError, ValueError) as exc:
                    failures.append({"job_id": preview.job_id, "error": str(exc)})
                    if fail_fast:
                        break
    ui_art = _artifacts_for_ui_export(
        export_ui_data,
        app_config_path=app_config_path,
        profile_path=profile_path,
        sources_path=sources_path,
    )
    summary = {
        "selected_jobs": selected_jobs,
        "processed": len(results),
        "failed": len(failures),
        "results": results,
        "failures": failures,
    }
    ok = len(failures) == 0
    if json_out:
        _emit_standard_envelope(
            json_out,
            "draft_applications",
            ok,
            summary=summary,
            artifacts={"ui_export": ui_art},
        )
    else:
        typer.echo(
            f"draft-applications complete: selected={selected_jobs} processed={len(results)} failed={len(failures)}"
        )
        for item in results:
            typer.echo(
                "job_id={job_id} readiness={readiness} cover_letter_origin={cover} answers_origin={answers} missing_inputs={missing}".format(
                    job_id=item["job_id"],
                    readiness=item["readiness"],
                    cover=item["cover_letter_origin"],
                    answers=item["answers_origin"],
                    missing=item["missing_inputs"],
                )
            )
        if failures:
            typer.echo("failed_jobs:")
            for item in failures:
                typer.echo(f"- {item['job_id']}: {item['error']}")
    _echo_ui_export_status(ui_art, enabled=export_ui_data)
    if failures:
        raise typer.Exit(code=1)


@apply_app.command("prepare")
def apply_prepare(
    job_id: str = typer.Option(...),
    questions_file: Path | None = typer.Option(None, exists=True, dir_okay=False),
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.yaml")),
    sources_path: Path = typer.Option(Path("config/sources.yaml"), "--sources-path", "--sources-dir"),
    application_state_root: Path = typer.Option(Path("state/applications")),
    export_ui_data: bool = typer.Option(
        True,
        "--export-ui-data/--no-export-ui-data",
        help="Run scripts/export_ui_data.sh after successful apply prepare (default: on).",
    ),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    app_config, profile, _sources, session_factory = _load_runtime(app_config_path, profile_path, sources_path)
    service = _application_service(application_state_root)
    with FileLock(_pipeline_lock_path(app_config)):
        with session_factory() as session:
            try:
                result = service.regenerate_application(session, profile, job_id=job_id, questions_file=questions_file)
            except (FileNotFoundError, ValueError) as exc:
                _emit_standard_envelope(json_out, "apply_prepare", False, errors=[str(exc)], text=f"apply prepare failed: {exc}")
                raise typer.Exit(code=1)
    ui_art = _artifacts_for_ui_export(
        export_ui_data,
        app_config_path=app_config_path,
        profile_path=profile_path,
        sources_path=sources_path,
    )
    _emit_standard_envelope(
        json_out,
        "apply_prepare",
        True,
        summary=result,
        artifacts={
            "application_state_root": str(application_state_root.resolve()),
            "ui_export": ui_art,
        },
        text=f"apply prepare complete: job_id={job_id}",
    )
    if not json_out:
        _echo_ui_export_status(ui_art, enabled=export_ui_data)


@apply_app.command("open")
def apply_open(
    job_id: str = typer.Option(...),
    mode: str = typer.Option(..., "--mode"),
    browser_profile: str | None = typer.Option(None, "--browser-profile"),
    overrides_file: Path | None = typer.Option(None, "--overrides-file", exists=True, dir_okay=False),
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.yaml")),
    sources_path: Path = typer.Option(Path("config/sources.yaml"), "--sources-path", "--sources-dir"),
    application_state_root: Path = typer.Option(Path("state/applications")),
    apply_state_root: Path = typer.Option(Path("state/apply_sessions")),
    export_ui_data: bool = typer.Option(
        True,
        "--export-ui-data/--no-export-ui-data",
        help="Run scripts/export_ui_data.sh after successful apply open (default: on).",
    ),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    app_config, profile, _sources, session_factory = _load_runtime(app_config_path, profile_path, sources_path)
    service = _apply_service(application_state_root, apply_state_root)
    with FileLock(_pipeline_lock_path(app_config)):
        with session_factory() as session:
            try:
                overrides = service.load_overrides_file(overrides_file)
                result = service.open_session(
                    session,
                    profile,
                    job_id=job_id,
                    mode=mode,
                    browser_profile=browser_profile,
                    overrides=overrides,
                )
            except (FileNotFoundError, ValueError, ValidationError, json.JSONDecodeError) as exc:
                _emit_standard_envelope(json_out, "apply_open", False, errors=[str(exc)], text=f"apply open failed: {exc}")
                raise typer.Exit(code=1)
    summary = result.session.model_dump(mode="json")
    summary.update(
        {
            "candidate_inputs": len(result.candidate_inputs),
            "unresolved_fields": len(result.unresolved_fields),
            "pending_approvals": len([item for item in result.approvals_required if item.status == "pending"]),
        }
    )
    ui_art = _artifacts_for_ui_export(
        export_ui_data,
        app_config_path=app_config_path,
        profile_path=profile_path,
        sources_path=sources_path,
    )
    _emit_standard_envelope(
        json_out,
        "apply_open",
        True,
        summary=summary,
        warnings=result.warnings,
        artifacts={
            "apply_state_root": str(apply_state_root.resolve()),
            "session_root": str((apply_state_root / job_id).resolve()),
            "browser_request": str((apply_state_root / job_id / "openclaw" / "browser.request.json").resolve()),
            "ui_export": ui_art,
        },
        text=f"apply open complete: job_id={job_id} mode={mode} status={result.session.status}",
    )
    if not json_out:
        _echo_ui_export_status(ui_art, enabled=export_ui_data)


@apply_app.command("status")
def apply_status(
    job_id: str = typer.Option(...),
    apply_state_root: Path = typer.Option(Path("state/apply_sessions")),
    application_state_root: Path = typer.Option(Path("state/applications")),
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.yaml")),
    sources_path: Path = typer.Option(Path("config/sources.yaml"), "--sources-path", "--sources-dir"),
    export_ui_data: bool = typer.Option(
        True,
        "--export-ui-data/--no-export-ui-data",
        help="Run scripts/export_ui_data.sh after successful apply status (default: on).",
    ),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    service = _apply_service(application_state_root, apply_state_root)
    try:
        status = service.get_status(job_id=job_id)
    except (FileNotFoundError, ValueError) as exc:
        _emit_standard_envelope(json_out, "apply_status", False, errors=[str(exc)], text=f"apply status failed: {exc}")
        raise typer.Exit(code=1)
    ui_art = _artifacts_for_ui_export(
        export_ui_data,
        app_config_path=app_config_path,
        profile_path=profile_path,
        sources_path=sources_path,
    )
    _emit_standard_envelope(
        json_out,
        "apply_status",
        True,
        summary={
            "session": status.session.model_dump(mode="json"),
            "filled_fields": [item.model_dump(mode="json") for item in status.filled_fields],
            "unresolved_fields": [item.model_dump(mode="json") for item in status.unresolved_fields],
            "approvals_required": [item.model_dump(mode="json") for item in status.approvals_required],
        },
        artifacts={"session_root": str((apply_state_root / job_id).resolve()), "ui_export": ui_art},
        text=f"apply status: job_id={job_id} status={status.session.status}",
    )
    if not json_out:
        _echo_ui_export_status(ui_art, enabled=export_ui_data)


@apply_app.command("resume")
def apply_resume(
    job_id: str = typer.Option(...),
    apply_state_root: Path = typer.Option(Path("state/apply_sessions")),
    application_state_root: Path = typer.Option(Path("state/applications")),
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.yaml")),
    sources_path: Path = typer.Option(Path("config/sources.yaml"), "--sources-path", "--sources-dir"),
    export_ui_data: bool = typer.Option(
        True,
        "--export-ui-data/--no-export-ui-data",
        help="Run scripts/export_ui_data.sh after successful apply resume (default: on).",
    ),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    service = _apply_service(application_state_root, apply_state_root)
    try:
        status = service.resume_session(job_id=job_id)
    except (FileNotFoundError, ValueError) as exc:
        _emit_standard_envelope(json_out, "apply_resume", False, errors=[str(exc)], text=f"apply resume failed: {exc}")
        raise typer.Exit(code=1)
    ui_art = _artifacts_for_ui_export(
        export_ui_data,
        app_config_path=app_config_path,
        profile_path=profile_path,
        sources_path=sources_path,
    )
    _emit_standard_envelope(
        json_out,
        "apply_resume",
        True,
        summary=status.session.model_dump(mode="json"),
        artifacts={
            "browser_request": str((apply_state_root / job_id / "openclaw" / "browser.request.json").resolve()),
            "ui_export": ui_art,
        },
        text=f"apply resume queued: job_id={job_id} status={status.session.status}",
    )
    if not json_out:
        _echo_ui_export_status(ui_art, enabled=export_ui_data)


@apply_app.command("approve")
def apply_approve(
    job_id: str = typer.Option(...),
    action_id: str = typer.Option(..., "--action"),
    apply_state_root: Path = typer.Option(Path("state/apply_sessions")),
    application_state_root: Path = typer.Option(Path("state/applications")),
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.yaml")),
    sources_path: Path = typer.Option(Path("config/sources.yaml"), "--sources-path", "--sources-dir"),
    export_ui_data: bool = typer.Option(
        True,
        "--export-ui-data/--no-export-ui-data",
        help="Run scripts/export_ui_data.sh after successful apply approve (default: on).",
    ),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    service = _apply_service(application_state_root, apply_state_root)
    try:
        status = service.approve_action(job_id=job_id, action_id=action_id)
    except (FileNotFoundError, ValueError) as exc:
        _emit_standard_envelope(json_out, "apply_approve", False, errors=[str(exc)], text=f"apply approve failed: {exc}")
        raise typer.Exit(code=1)
    ui_art = _artifacts_for_ui_export(
        export_ui_data,
        app_config_path=app_config_path,
        profile_path=profile_path,
        sources_path=sources_path,
    )
    _emit_standard_envelope(
        json_out,
        "apply_approve",
        True,
        summary={
            "job_id": job_id,
            "action_id": action_id,
            "status": status.session.status,
            "approved_action_ids": status.session.approved_action_ids,
        },
        artifacts={"ui_export": ui_art},
        text=f"apply approve complete: job_id={job_id} action={action_id}",
    )
    if not json_out:
        _echo_ui_export_status(ui_art, enabled=export_ui_data)


@apply_app.command("cancel")
def apply_cancel(
    job_id: str = typer.Option(...),
    apply_state_root: Path = typer.Option(Path("state/apply_sessions")),
    application_state_root: Path = typer.Option(Path("state/applications")),
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.yaml")),
    sources_path: Path = typer.Option(Path("config/sources.yaml"), "--sources-path", "--sources-dir"),
    export_ui_data: bool = typer.Option(
        True,
        "--export-ui-data/--no-export-ui-data",
        help="Run scripts/export_ui_data.sh after successful apply cancel (default: on).",
    ),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    service = _apply_service(application_state_root, apply_state_root)
    try:
        status = service.cancel_session(job_id=job_id)
    except (FileNotFoundError, ValueError) as exc:
        _emit_standard_envelope(json_out, "apply_cancel", False, errors=[str(exc)], text=f"apply cancel failed: {exc}")
        raise typer.Exit(code=1)
    ui_art = _artifacts_for_ui_export(
        export_ui_data,
        app_config_path=app_config_path,
        profile_path=profile_path,
        sources_path=sources_path,
    )
    _emit_standard_envelope(
        json_out,
        "apply_cancel",
        True,
        summary=status.session.model_dump(mode="json"),
        artifacts={"ui_export": ui_art},
        text=f"apply cancel complete: job_id={job_id}",
    )
    if not json_out:
        _echo_ui_export_status(ui_art, enabled=export_ui_data)


@apply_app.command("report")
def apply_report(
    job_id: str = typer.Option(...),
    apply_state_root: Path = typer.Option(Path("state/apply_sessions")),
    application_state_root: Path = typer.Option(Path("state/applications")),
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.yaml")),
    sources_path: Path = typer.Option(Path("config/sources.yaml"), "--sources-path", "--sources-dir"),
    export_ui_data: bool = typer.Option(
        True,
        "--export-ui-data/--no-export-ui-data",
        help="Run scripts/export_ui_data.sh after successful apply report (default: on).",
    ),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    service = _apply_service(application_state_root, apply_state_root)
    try:
        report = service.render_report(job_id=job_id)
        status = service.get_status(job_id=job_id)
    except (FileNotFoundError, ValueError) as exc:
        _emit_standard_envelope(json_out, "apply_report", False, errors=[str(exc)], text=f"apply report failed: {exc}")
        raise typer.Exit(code=1)
    ui_art = _artifacts_for_ui_export(
        export_ui_data,
        app_config_path=app_config_path,
        profile_path=profile_path,
        sources_path=sources_path,
    )
    _emit_standard_envelope(
        json_out,
        "apply_report",
        True,
        summary={"job_id": job_id, "status": status.session.status, "report_markdown": report},
        artifacts={
            "report_path": str((apply_state_root / job_id / "apply_report.md").resolve()),
            "ui_export": ui_art,
        },
        text=None if json_out else report,
    )
    if not json_out:
        _echo_ui_export_status(ui_art, enabled=export_ui_data)


@apply_app.command("list")
def apply_list(
    apply_state_root: Path = typer.Option(Path("state/apply_sessions")),
    application_state_root: Path = typer.Option(Path("state/applications")),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    service = _apply_service(application_state_root, apply_state_root)
    rows = service.list_sessions()
    if json_out:
        _emit_standard_envelope(
            True,
            "apply_list",
            True,
            summary={"rows": [item.model_dump(mode="json") for item in rows], "count": len(rows)},
            artifacts={"apply_state_root": str(apply_state_root.resolve())},
        )
        return
    for row in rows:
        typer.echo(
            f"{row.job_id}\t{row.mode}\t{row.status}\tpending={row.pending_approvals}\tunresolved={row.unresolved_fields}"
        )


@apply_app.command("browser-run")
def apply_browser_run(
    job_id: str | None = typer.Option(None, "--job-id"),
    request_file: Path | None = typer.Option(None, "--request-file", exists=True, dir_okay=False),
    apply_state_root: Path = typer.Option(Path("state/apply_sessions")),
    backend: str = typer.Option("playwright", "--backend"),
    browser_profile_dir: Path | None = typer.Option(None, "--browser-profile-dir", dir_okay=True, file_okay=False),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    if request_file is None and job_id is None:
        _emit_standard_envelope(json_out, "apply_browser_run", False, errors=["job_id_or_request_file_required"], text="apply browser-run failed: job_id_or_request_file_required")
        raise typer.Exit(code=1)
    try:
        if request_file is None:
            assert job_id is not None
            request_root = apply_state_root / job_id / "openclaw"
        else:
            request_root = request_file.parent
            if job_id is None:
                job_id = request_root.parent.name
        client = FilesystemApplyOpenClawClient(request_root)
        request = client.load_browser_request()
        runner = ApplyBrowserRunner(build_browser_backend(backend))
        result = runner.run(request, browser_profile_dir=browser_profile_dir)
        client.export_browser_result(result)
    except Exception as exc:
        _emit_standard_envelope(json_out, "apply_browser_run", False, errors=[str(exc)], text=f"apply browser-run failed: {exc}")
        raise typer.Exit(code=1)
    _emit_standard_envelope(
        json_out,
        "apply_browser_run",
        True,
        summary=result.model_dump(mode="json"),
        artifacts={"browser_result": str((request_root / "browser.result.json").resolve())},
        text=f"apply browser-run complete: job_id={request.job_id} step={result.step_label} submit_available={result.submit_available}",
    )


@profile_app.command("import")
def profile_import(
    file: Path | None = typer.Option(None, exists=True, dir_okay=False),
    text: str | None = typer.Option(None),
    answers_file: Path | None = typer.Option(None, exists=True, dir_okay=False),
    answers_text: str | None = typer.Option(None),
    state_root: Path = typer.Option(Path("state/profile_bootstrap")),
    config_root: Path = typer.Option(Path("config")),
) -> None:
    service = _profile_service(state_root, config_root)
    try:
        resolved_answers = _resolve_answers_text(answers_file, answers_text)
        if file is None and text is None:
            metadata = service.refresh_pending_import(refinement_answers=resolved_answers)
        else:
            metadata = service.import_resume(
                file_path=file,
                pasted_text=text,
                reimport=False,
                refinement_answers=resolved_answers,
            )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        typer.echo(f"profile import failed: {exc}")
        raise typer.Exit(code=1)
    typer.echo(f"profile import complete: import_id={metadata.import_id} pending={metadata.extraction_pending}")


@profile_app.command("reimport")
def profile_reimport(
    file: Path | None = typer.Option(None, exists=True, dir_okay=False),
    text: str | None = typer.Option(None),
    answers_file: Path | None = typer.Option(None, exists=True, dir_okay=False),
    answers_text: str | None = typer.Option(None),
    state_root: Path = typer.Option(Path("state/profile_bootstrap")),
    config_root: Path = typer.Option(Path("config")),
) -> None:
    service = _profile_service(state_root, config_root)
    try:
        resolved_answers = _resolve_answers_text(answers_file, answers_text)
        if file is None and text is None:
            metadata = service.refresh_pending_import(refinement_answers=resolved_answers)
        else:
            metadata = service.import_resume(
                file_path=file,
                pasted_text=text,
                reimport=True,
                refinement_answers=resolved_answers,
            )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        typer.echo(f"profile reimport failed: {exc}")
        raise typer.Exit(code=1)
    typer.echo(f"profile reimport complete: import_id={metadata.import_id} pending={metadata.extraction_pending}")


@profile_app.command("show-draft")
def profile_show_draft(
    state_root: Path = typer.Option(Path("state/profile_bootstrap")),
    config_root: Path = typer.Option(Path("config")),
) -> None:
    service = _profile_service(state_root, config_root)
    try:
        profile = service.load_profile_draft()
    except FileNotFoundError as exc:
        typer.echo(f"profile show-draft failed: {exc}")
        raise typer.Exit(code=1)
    typer.echo(profile.model_dump_json(indent=2))


@profile_app.command("missing")
def profile_missing(
    state_root: Path = typer.Option(Path("state/profile_bootstrap")),
    config_root: Path = typer.Option(Path("config")),
) -> None:
    service = _profile_service(state_root, config_root)
    try:
        report = service.load_missing_fields()
    except FileNotFoundError as exc:
        typer.echo(f"profile missing failed: {exc}")
        raise typer.Exit(code=1)
    typer.echo(report.model_dump_json(indent=2))


@profile_app.command("validate-draft")
def profile_validate_draft(
    state_root: Path = typer.Option(Path("state/profile_bootstrap")),
    config_root: Path = typer.Option(Path("config")),
) -> None:
    service = _profile_service(state_root, config_root)
    try:
        result = service.validate_draft()
    except FileNotFoundError as exc:
        typer.echo(f"profile validate-draft failed: {exc}")
        raise typer.Exit(code=1)
    if result.errors:
        typer.echo(f"profile draft invalid: status={result.status} errors={result.errors}")
        raise typer.Exit(code=1)
    typer.echo(f"profile draft valid: status={result.status}")


@profile_app.command("diff")
def profile_diff(
    state_root: Path = typer.Option(Path("state/profile_bootstrap")),
    config_root: Path = typer.Option(Path("config")),
) -> None:
    service = _profile_service(state_root, config_root)
    try:
        diff = service.diff_draft()
    except FileNotFoundError as exc:
        typer.echo(f"profile diff failed: {exc}")
        raise typer.Exit(code=1)
    typer.echo(diff.model_dump_json(indent=2))


@profile_app.command("promote-draft")
def profile_promote_draft(
    state_root: Path = typer.Option(Path("state/profile_bootstrap")),
    config_root: Path = typer.Option(Path("config")),
) -> None:
    service = _profile_service(state_root, config_root)
    try:
        diff = service.promote_draft()
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(f"profile promote-draft failed: {exc}")
        raise typer.Exit(code=1)
    typer.echo(f"profile draft promoted: safe_updates={len(diff.safe_auto_updates)}")


@profile_app.command("set")
def profile_set(
    profile_path: Path = typer.Option(Path("config/profile.yaml")),
    full_name: str | None = typer.Option(None),
    headline: str | None = typer.Option(None),
    email: str | None = typer.Option(None),
    phone: str | None = typer.Option(None),
    location_text: str | None = typer.Option(None),
    github_url: str | None = typer.Option(None),
    linkedin_url: str | None = typer.Option(None),
    years_experience: int | None = typer.Option(None),
    summary: str | None = typer.Option(None),
    add_target_title: list[str] = typer.Option([], "--add-target-title"),
    remove_target_title: list[str] = typer.Option([], "--remove-target-title"),
    add_required_skill: list[str] = typer.Option([], "--add-required-skill"),
    remove_required_skill: list[str] = typer.Option([], "--remove-required-skill"),
    add_preferred_skill: list[str] = typer.Option([], "--add-preferred-skill"),
    remove_preferred_skill: list[str] = typer.Option([], "--remove-preferred-skill"),
    add_preferred_location: list[str] = typer.Option([], "--add-preferred-location"),
    remove_preferred_location: list[str] = typer.Option([], "--remove-preferred-location"),
    add_allowed_country: list[str] = typer.Option([], "--add-allowed-country"),
    remove_allowed_country: list[str] = typer.Option([], "--remove-allowed-country"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    raw = load_yaml(profile_path) if profile_path.exists() else {}
    if not isinstance(raw, dict):
        raw = {}
    payload = dict(raw)
    for field, value in [
        ("full_name", full_name),
        ("headline", headline),
        ("email", email),
        ("phone", phone),
        ("location_text", location_text),
        ("github_url", github_url),
        ("linkedin_url", linkedin_url),
        ("years_experience", years_experience),
        ("summary", summary),
    ]:
        if value is not None:
            payload[field] = value
    list_ops = [
        ("target_titles", add_target_title, remove_target_title),
        ("required_skills", add_required_skill, remove_required_skill),
        ("preferred_skills", add_preferred_skill, remove_preferred_skill),
        ("preferred_locations", add_preferred_location, remove_preferred_location),
        ("allowed_countries", add_allowed_country, remove_allowed_country),
    ]
    for field, adds, removes in list_ops:
        current = [str(item) for item in payload.get(field, []) or []]
        current.extend(adds)
        remove_keys = {item.casefold() for item in removes}
        current = [item for item in current if item.casefold() not in remove_keys]
        payload[field] = _dedupe_str_list(current)
    try:
        draft = ProfileConfigDraft.model_validate(payload)
    except ValidationError as exc:
        _emit_json(json_out, {"command": "profile_set", "status": "failed", "error": str(exc)}, f"profile set failed: {exc}")
        raise typer.Exit(code=1)
    persisted = {
        "version": draft.version,
        "full_name": draft.full_name,
        "headline": draft.headline,
        "email": draft.email,
        "phone": draft.phone,
        "location_text": draft.location_text,
        "github_url": draft.github_url,
        "linkedin_url": draft.linkedin_url,
        "years_experience": draft.years_experience,
        "summary": draft.summary,
        "strengths": draft.strengths,
        "recent_titles": draft.recent_titles,
        "recent_companies": draft.recent_companies,
        "target_titles": draft.target_titles,
        "required_skills": draft.required_skills,
        "preferred_skills": draft.preferred_skills,
        "preferred_locations": draft.preferred_locations,
        "allowed_countries": draft.allowed_countries,
    }
    dump_yaml(persisted, profile_path)
    _emit_json(
        json_out,
        {"command": "profile_set", "status": "ok", "profile_path": str(profile_path.resolve())},
        f"profile set: wrote {profile_path}",
    )


@profile_app.command("show")
def profile_show(
    profile_path: Path = typer.Option(Path("config/profile.yaml")),
    json_out: bool = typer.Option(False, "--json", help="Emit canonical merged profile as structured JSON."),
) -> None:
    try:
        resolved = resolve_profile_config_path(profile_path)
    except FileNotFoundError as exc:
        typer.echo(f"profile show failed: {exc}")
        raise typer.Exit(code=1)
    profile = load_profile_config(profile_path)
    if json_out:
        _emit_standard_envelope(
            json_out,
            "profile_show",
            True,
            summary={"profile": profile.model_dump(mode="json")},
            artifacts={"profile_path": str(resolved.resolve())},
        )
    else:
        typer.echo(json.dumps(profile.model_dump(mode="json"), indent=2, default=str))


@sources_app.command("list")
def sources_list(
    sources_path: Path = typer.Option(
        Path("config/sources.yaml"),
        "--sources-path",
        "--sources-dir",
        help="Canonical source config file (YAML)",
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    """List validated sources from canonical sources.yaml."""
    try:
        rows = list_sources(sources_path)
    except (ValidationError, OSError, ValueError) as exc:
        _emit_json(json_out, {"command": "sources_list", "status": "failed", "error": str(exc)}, f"sources list failed: {exc}")
        raise typer.Exit(code=1)
    if json_out:
        _emit_standard_envelope(
            json_out,
            "sources_list",
            True,
            summary={"sources": [cfg.model_dump(mode="json") for cfg in rows]},
            artifacts={"sources_path": str(sources_path.resolve())},
        )
        return
    for cfg in rows:
        typer.echo(f"{cfg.name}\t{cfg.kind}\tenabled={cfg.enabled}")


@sources_app.command("add")
def sources_add(
    sources_path: Path = typer.Option(
        Path("config/sources.yaml"),
        "--sources-path",
        "--sources-dir",
        help="Canonical source config file (YAML)",
    ),
    json_body: str | None = typer.Option(
        None,
        "--json",
        help='One JSON object matching SourceConfig, e.g. {"name":"acme","kind":"rss","feed_url":"https://example.com/jobs.xml"}',
    ),
    json_file: Path | None = typer.Option(
        None,
        "--json-file",
        exists=True,
        dir_okay=False,
        help="Path to a JSON file containing one source object",
    ),
    force: bool = typer.Option(False, "--force", help="Replace existing source with same name"),
    json_out: bool = typer.Option(False, "--json-out"),
) -> None:
    """Add or replace one source definition inside config/sources.yaml."""
    if (json_body is None) == (json_file is None):
        _emit_json(json_out, {"command": "sources_add", "status": "failed", "error": "missing_payload"}, "sources add: pass exactly one of --json or --json-file")
        raise typer.Exit(code=1)
    raw = json_file.read_text(encoding="utf-8") if json_file is not None else json_body
    assert raw is not None
    try:
        config = parse_source_json_payload(raw)
    except ValueError as exc:
        _emit_json(json_out, {"command": "sources_add", "status": "failed", "error": str(exc)}, f"sources add failed: {exc}")
        raise typer.Exit(code=1)
    try:
        add_source(sources_path, config, replace=force)
    except ValueError as exc:
        _emit_json(json_out, {"command": "sources_add", "status": "failed", "error": str(exc)}, f"sources add failed: {exc}")
        raise typer.Exit(code=1)
    _emit_json(
        json_out,
        {"command": "sources_add", "status": "ok", "source_name": config.name, "sources_path": str(sources_path.resolve())},
        f"sources add: wrote {sources_path.resolve()}",
    )


@sources_app.command("set")
def sources_set(
    name: str = typer.Argument(...),
    sources_path: Path = typer.Option(Path("config/sources.yaml"), "--sources-path", "--sources-dir"),
    enabled: bool | None = typer.Option(None, "--enabled/--disabled"),
    priority: int | None = typer.Option(None, min=0),
    trust_weight: float | None = typer.Option(None, min=0.0001),
    fetch_cap: int | None = typer.Option(None, min=1),
    add_blocked_title_keyword: list[str] = typer.Option([], "--add-blocked-title-keyword"),
    remove_blocked_title_keyword: list[str] = typer.Option([], "--remove-blocked-title-keyword"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    try:
        updated = set_source_fields(
            sources_path,
            name=name,
            enabled=enabled,
            priority=priority,
            trust_weight=trust_weight,
            fetch_cap=fetch_cap,
            add_blocked_title_keywords=add_blocked_title_keyword,
            remove_blocked_title_keywords=remove_blocked_title_keyword,
        )
    except ValueError as exc:
        _emit_json(json_out, {"command": "sources_set", "status": "failed", "error": str(exc)}, f"sources set failed: {exc}")
        raise typer.Exit(code=1)
    _emit_json(
        json_out,
        {"command": "sources_set", "status": "ok", "source": updated.model_dump(mode="json")},
        f"sources set: updated {updated.name}",
    )


@sources_app.command("disable")
def sources_disable(
    name: str = typer.Argument(...),
    sources_path: Path = typer.Option(Path("config/sources.yaml"), "--sources-path", "--sources-dir"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    try:
        updated = disable_source(sources_path, name=name)
    except ValueError as exc:
        _emit_json(json_out, {"command": "sources_disable", "status": "failed", "error": str(exc)}, f"sources disable failed: {exc}")
        raise typer.Exit(code=1)
    _emit_json(
        json_out,
        {"command": "sources_disable", "status": "ok", "source": updated.model_dump(mode="json")},
        f"sources disable: {updated.name}",
    )


@sources_app.command("remove")
def sources_remove(
    name: str = typer.Argument(...),
    sources_path: Path = typer.Option(Path("config/sources.yaml"), "--sources-path", "--sources-dir"),
    yes: bool = typer.Option(False, "--yes"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    if not yes:
        _emit_json(json_out, {"command": "sources_remove", "status": "failed", "error": "confirmation_required"}, "sources remove failed: pass --yes")
        raise typer.Exit(code=1)
    try:
        remove_source(sources_path, name=name)
    except ValueError as exc:
        _emit_json(json_out, {"command": "sources_remove", "status": "failed", "error": str(exc)}, f"sources remove failed: {exc}")
        raise typer.Exit(code=1)
    _emit_json(
        json_out,
        {"command": "sources_remove", "status": "ok", "removed": name, "sources_path": str(sources_path.resolve())},
        f"sources remove: {name}",
    )


def _set_list_with_ops(current: object, adds: list[str], removes: list[str]) -> list[str]:
    values = [str(item) for item in (current or [])]
    values.extend(adds)
    remove_keys = {item.casefold() for item in removes}
    values = [item for item in values if item.casefold() not in remove_keys]
    return _dedupe_str_list(values)


def _dedupe_str_list(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in values:
        cleaned = str(item).strip()
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return out


def _split_family_pattern(value: str, *, kind: str) -> tuple[str, str]:
    if ":" not in value:
        raise ValueError(f"{kind}_invalid:{value}")
    family, pattern = value.split(":", 1)
    family = family.strip()
    pattern = pattern.strip()
    if not family or not pattern:
        raise ValueError(f"{kind}_invalid:{value}")
    return family, pattern


def main() -> None:
    app()


if __name__ == "__main__":
    main()


def _resolve_answers_text(answers_file: Path | None, answers_text: str | None) -> str | None:
    if answers_file is not None and answers_text is not None:
        raise ValueError("provide either answers_file or answers_text")
    if answers_file is not None:
        return answers_file.read_text(encoding="utf-8")
    return answers_text


def _record_from_existing(source_job: SourceJob, source: Source, normalized: NormalizedJob) -> SourceJobRecord:
    payload = source_job.payload_json or {}
    return SourceJobRecord(
        source_job_key=source_job.source_job_key,
        source_url=source_job.source_url,
        apply_url=source_job.apply_url,
        source_company_id=payload.get("source_company_id"),
        title=str(payload.get("title") or normalized.title),
        company=str(payload.get("company") or normalized.company_name or source.name),
        location_text=str(payload.get("location_text") or normalized.location_text or ""),
        posted_at_raw=payload.get("published") or payload.get("posted_at") or normalized.posted_at.isoformat() if normalized.posted_at else None,
        employment_type_raw=payload.get("employment_type") or normalized.employment_type,
        seniority_raw=payload.get("seniority") or normalized.seniority,
        salary_raw=payload.get("salary") or payload.get("salary_raw"),
        description_raw=payload.get("summary") or payload.get("description") or normalized.description_text,
        tags_raw=payload.get("tags") or normalized.tags_json,
        raw_payload=payload,
    )
