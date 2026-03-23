"""Composed operator workflows (onboarding, daily-run) — thin orchestration over the CLI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from findmejobs.cli.json_envelope import cli_envelope, meta_standard
from findmejobs.config.loader import (
    ensure_directories,
    load_app_config,
    load_profile_config,
    load_source_configs,
    resolve_profile_config_path,
)
from findmejobs.observability.logging import configure_logging
from findmejobs.profile_bootstrap.extractor import prepare_paths


def _base_cmd() -> list[str]:
    return [sys.executable, "-m", "findmejobs"]


def _parse_stdout_json(stdout: str) -> Any | None:
    text = (stdout or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


@dataclass
class SubprocessResult:
    exit_code: int
    stdout: str
    stderr: str

    @property
    def parsed_json(self) -> Any | None:
        return _parse_stdout_json(self.stdout)


def default_subprocess_runner(argv: list[str], *, cwd: Path | None = None) -> SubprocessResult:
    proc = subprocess.run(
        argv,
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
        check=False,
        env=os.environ.copy(),
    )
    return SubprocessResult(proc.returncode, proc.stdout or "", proc.stderr or "")


def _common_opts(
    app_config_path: Path,
    profile_path: Path,
    sources_path: Path,
) -> list[str]:
    return [
        "--app-config-path",
        str(app_config_path),
        "--profile-path",
        str(profile_path),
        "--sources-path",
        str(sources_path),
    ]


def run_onboarding_workflow(
    *,
    config_root: Path,
    app_config_path: Path,
    profile_path: Path,
    sources_path: Path,
    profile_bootstrap_state: Path,
    dry_run: bool,
    resume_file: Path | None,
    runner: Callable[[list[str], Path | None], SubprocessResult] | None = None,
) -> dict[str, Any]:
    run = runner or (lambda argv, cwd: default_subprocess_runner(argv, cwd=cwd))
    steps: list[dict[str, Any]] = []
    warnings: list[str] = []
    errors: list[str] = []
    artifacts: dict[str, Any] = {"config_root": str(config_root.resolve())}

    def record(step: str, ok: bool, **fields: Any) -> None:
        steps.append({"step": step, "ok": ok, **fields})

    bootstrap_paths = prepare_paths(profile_bootstrap_state, config_root)
    draft_exists = bootstrap_paths.profile_draft_path.exists()
    artifacts["profile_bootstrap"] = {
        "state_root": str(profile_bootstrap_state.resolve()),
        "draft_profile_path": str(bootstrap_paths.profile_draft_path),
        "draft_exists": draft_exists,
    }

    if dry_run:
        record("config_validate", True, planned=True)
        record("directory_ensure", True, planned=True)
        record("canonical_config_check", True, planned=True)
        record("profile_bootstrap_surface", True, planned=True, draft_exists=draft_exists)
        record("doctor", True, planned=True)
        if resume_file is not None:
            record("profile_import", True, planned=True, resume_file=str(resume_file))
        summary = {
            "dry_run": True,
            "steps": steps,
            "next_actions": _onboarding_next_actions(
                config_root=config_root,
                app_config_path=app_config_path,
                profile_path=profile_path,
                sources_path=sources_path,
                draft_exists=draft_exists,
                resume_file=resume_file,
                ran_import=False,
            ),
        }
        return cli_envelope("onboarding_run", True, summary=summary, warnings=warnings, errors=[], artifacts=artifacts, meta=meta_standard())

    argv_base = _base_cmd()
    cwd: Path | None = Path.cwd()

    val = run(
        [*argv_base, "config", "validate", "--json", *_common_opts(app_config_path, profile_path, sources_path)],
        cwd,
    )
    parsed_val = val.parsed_json
    record(
        "config_validate",
        val.exit_code == 0,
        exit_code=val.exit_code,
        envelope=parsed_val if isinstance(parsed_val, dict) else None,
        stderr_tail=val.stderr[-500:] if val.stderr else "",
    )

    if val.exit_code != 0:
        init = run([*argv_base, "config", "init", "--json", "--config-root", str(config_root)], cwd)
        record(
            "config_init",
            init.exit_code == 0,
            exit_code=init.exit_code,
            envelope=init.parsed_json if isinstance(init.parsed_json, dict) else None,
            error=(init.stderr or init.stdout or "config_init_failed")[-300:],
        )
        val = run(
            [*argv_base, "config", "validate", "--json", *_common_opts(app_config_path, profile_path, sources_path)],
            cwd,
        )
        record(
            "config_validate_retry",
            val.exit_code == 0,
            exit_code=val.exit_code,
            envelope=val.parsed_json if isinstance(val.parsed_json, dict) else None,
        )

    if val.exit_code != 0:
        errors.append("config_validate_failed")
        summary = {
            "dry_run": False,
            "steps": steps,
            "next_actions": ["fix_config_validation_errors", "rerun_findmejobs_onboarding_run"],
        }
        return cli_envelope("onboarding_run", False, summary=summary, warnings=warnings, errors=errors, artifacts=artifacts, meta=meta_standard())

    try:
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
        record("directory_ensure", True, paths_created=True)
    except Exception as exc:  # noqa: BLE001
        record("directory_ensure", False, error=str(exc))
        errors.append(f"directory_ensure:{exc}")
        summary = {"dry_run": False, "steps": steps, "next_actions": ["fix_app_config_storage_paths"]}
        return cli_envelope("onboarding_run", False, summary=summary, warnings=warnings, errors=errors, artifacts=artifacts, meta=meta_standard())

    canon_ok = True
    canon_notes: list[str] = []
    if not app_config_path.exists():
        canon_ok = False
        canon_notes.append("missing_app_toml")
    if not profile_path.exists():
        canon_ok = False
        canon_notes.append("missing_profile_yaml")
    ranking_path = profile_path.with_name("ranking.yaml")
    if not ranking_path.exists():
        canon_ok = False
        canon_notes.append("missing_ranking_yaml")
    if not sources_path.exists():
        canon_notes.append("missing_sources_yaml")
    record("canonical_config_check", canon_ok, notes=canon_notes)
    if not canon_ok:
        warnings.extend(canon_notes)

    profile_load_ok = True
    try:
        load_profile_config(resolve_profile_config_path(profile_path))
    except Exception as exc:  # noqa: BLE001
        profile_load_ok = False
        warnings.append(f"profile_load_failed:{exc}")
    record(
        "profile_bootstrap_surface",
        profile_load_ok,
        draft_exists=draft_exists,
        canonical_profile_load_ok=profile_load_ok,
    )

    doc = run([*argv_base, "doctor", "--json", *_common_opts(app_config_path, profile_path, sources_path)], cwd)
    doc_env = doc.parsed_json
    record(
        "doctor",
        doc.exit_code == 0,
        exit_code=doc.exit_code,
        envelope=doc_env if isinstance(doc_env, dict) else None,
    )
    if doc.exit_code != 0:
        errors.append("doctor_failed")

    ran_import = False
    if resume_file is not None:
        imp = run(
            [
                *argv_base,
                "profile",
                "import",
                "--file",
                str(resume_file.resolve()),
                "--state-root",
                str(profile_bootstrap_state.resolve()),
                "--config-root",
                str(config_root.resolve()),
            ],
            cwd,
        )
        ran_import = True
        record(
            "profile_import",
            imp.exit_code == 0,
            exit_code=imp.exit_code,
            stderr_tail=imp.stderr[-400:] if imp.stderr else "",
        )
        if imp.exit_code != 0:
            errors.append("profile_import_failed")

    ok = not errors
    summary = {
        "dry_run": False,
        "steps": steps,
        "next_actions": _onboarding_next_actions(
            config_root=config_root,
            app_config_path=app_config_path,
            profile_path=profile_path,
            sources_path=sources_path,
            draft_exists=bootstrap_paths.profile_draft_path.exists(),
            resume_file=resume_file,
            ran_import=ran_import,
        ),
    }
    return cli_envelope("onboarding_run", ok, summary=summary, warnings=warnings, errors=errors, artifacts=artifacts, meta=meta_standard())


def _onboarding_next_actions(
    *,
    config_root: Path,
    app_config_path: Path,
    profile_path: Path,
    sources_path: Path,
    draft_exists: bool,
    resume_file: Path | None,
    ran_import: bool,
) -> list[str]:
    actions: list[str] = []
    try:
        src_cfgs = load_source_configs(sources_path) if sources_path.exists() else []
    except Exception:
        src_cfgs = []
    if not src_cfgs or not any(s.enabled for s in src_cfgs):
        actions.append("add_sources_via_findmejobs_sources_add")
    if draft_exists:
        actions.append("profile_validate_draft_then_profile_promote_draft")
    try:
        load_profile_config(resolve_profile_config_path(profile_path))
    except Exception:
        actions.append("fix_profile_yaml_and_ranking_yaml_or_run_profile_import")
    if resume_file is None and not ran_import:
        actions.append("optional_profile_import_when_resume_ready")
    if not actions:
        actions.append("run_findmejobs_daily_run")
    return actions


def run_daily_run_workflow(
    *,
    app_config_path: Path,
    profile_path: Path,
    sources_path: Path,
    dry_run: bool,
    send_digest: bool,
    skip_digest: bool,
    runner: Callable[[list[str], Path | None], SubprocessResult] | None = None,
) -> dict[str, Any]:
    run = runner or (lambda argv, cwd: default_subprocess_runner(argv, cwd=cwd))
    steps: list[dict[str, Any]] = []
    warnings: list[str] = []
    errors: list[str] = []
    artifacts: dict[str, Any] = {}

    digest_wanted = not skip_digest

    if dry_run:
        try:
            app_config = load_app_config(app_config_path)
            auto_digest = bool(app_config.delivery.email.enabled)
        except Exception:
            auto_digest = False
        will_send = digest_wanted and (auto_digest or send_digest)
        steps.append({"step": "ingest", "ok": True, "planned": True})
        steps.append({"step": "rank", "ok": True, "planned": True})
        steps.append({"step": "review_export", "ok": True, "planned": True})
        steps.append({"step": "digest_send", "ok": True, "planned": will_send, "skipped_reason": None if will_send else "digest_not_planned"})
        summary = {
            "dry_run": True,
            "steps": steps,
            "digest_would_send": will_send,
        }
        return cli_envelope("daily_run", True, summary=summary, warnings=warnings, errors=[], artifacts=artifacts, meta=meta_standard())

    argv_base = _base_cmd()
    opts = _common_opts(app_config_path, profile_path, sources_path)
    cwd: Path | None = Path.cwd()

    for cmd_name, argv in (
        ("ingest", [*argv_base, "ingest", "--json", *opts]),
        ("rank", [*argv_base, "rank", "--json", *opts]),
        ("review_export", [*argv_base, "review", "export", "--json", *opts]),
    ):
        res = run(argv, cwd)
        env = res.parsed_json
        row = {
            "step": cmd_name,
            "ok": res.exit_code == 0,
            "exit_code": res.exit_code,
            "envelope": env if isinstance(env, dict) else None,
            "stderr_tail": res.stderr[-400:] if res.stderr else "",
        }
        steps.append(row)
        if res.exit_code != 0:
            errors.append(f"{cmd_name}_failed")
            summary = {"dry_run": False, "steps": steps, "stopped_after": cmd_name}
            return cli_envelope("daily_run", False, summary=summary, warnings=warnings, errors=errors, artifacts=artifacts, meta=meta_standard())

    try:
        app_config = load_app_config(app_config_path)
        auto_digest = bool(app_config.delivery.email.enabled)
    except Exception as exc:  # noqa: BLE001
        auto_digest = False
        warnings.append(f"app_config_digest_hint:{exc}")

    should_send = digest_wanted and (auto_digest or send_digest)
    if should_send:
        res = run([*argv_base, "digest", "send", "--json", *opts], cwd)
        env = res.parsed_json
        steps.append(
            {
                "step": "digest_send",
                "ok": res.exit_code == 0,
                "exit_code": res.exit_code,
                "envelope": env if isinstance(env, dict) else None,
                "stderr_tail": res.stderr[-400:] if res.stderr else "",
            }
        )
        if res.exit_code != 0:
            errors.append("digest_send_failed")
    else:
        reason = "skip_digest_flag" if skip_digest else "email_disabled_and_no_send_digest_flag"
        steps.append({"step": "digest_send", "ok": True, "skipped": True, "skipped_reason": reason})

    ok = not errors
    summary = {"dry_run": False, "steps": steps, "digest_sent": should_send and ok}
    return cli_envelope("daily_run", ok, summary=summary, warnings=warnings, errors=errors, artifacts=artifacts, meta=meta_standard())
