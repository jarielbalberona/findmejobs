#!/usr/bin/env python3
"""Generate a markdown profile and application-packet audit (operator diagnostics)."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from sqlalchemy import select

from findmejobs.application.service import ApplicationDraftService
from findmejobs.config.loader import load_app_config, load_profile_config
from findmejobs.db.models import NormalizedJob
from findmejobs.db.session import create_session_factory


def _sanitized_profile_dump(profile) -> dict:
    data = profile.model_dump(mode="json")
    return data


def _try_packet_and_letter(root: Path, profile):
    app_path = root / "config" / "app.toml"
    if not app_path.exists():
        return None, None, None
    app_config = load_app_config(app_path)
    session_factory = create_session_factory(app_config.database.url)
    with session_factory() as session:
        job_ids = session.scalars(
            select(NormalizedJob.id)
            .where(NormalizedJob.normalization_status == "valid")
            .order_by(NormalizedJob.last_seen_at.desc())
            .limit(25)
        ).all()
        if not job_ids:
            return None, None, None
        with tempfile.TemporaryDirectory() as tmp:
            svc = ApplicationDraftService(state_root=Path(tmp) / "applications")
            for job_id in job_ids:
                try:
                    packet, _missing = svc.prepare_application(
                        session,
                        profile,
                        job_id=job_id,
                        questions_file=None,
                        snapshot_existing=False,
                    )
                except ValueError:
                    continue
                letter = svc._build_local_cover_letter(packet, [])
                return job_id, packet, letter
            return None, None, None


def main() -> None:
    parser = argparse.ArgumentParser(description="Write profile audit markdown.")
    parser.add_argument("--root", type=Path, default=Path("."), help="Project root (expects config/profile.yaml + ranking.yaml).")
    parser.add_argument("--output", type=Path, required=True, help="Markdown output path.")
    args = parser.parse_args()
    root = args.root.resolve()
    profile_path = root / "config" / "profile.yaml"
    profile = load_profile_config(profile_path)
    job_id, packet, letter = _try_packet_and_letter(root, profile)

    lines: list[str] = []
    lines.append("# Profile audit report")
    lines.append("")
    lines.append("## Runtime profile source of truth")
    lines.append("")
    lines.append(f"- Loader: `findmejobs.config.loader.load_profile_config` (see `src/findmejobs/config/loader.py`).")
    lines.append(f"- Canonical paths: `{profile_path}` plus sibling `config/ranking.yaml`.")
    lines.append("")
    lines.append("## Effective ProfileConfig (sanitized JSON)")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(_sanitized_profile_dump(profile), indent=2, default=str))
    lines.append("```")
    lines.append("")
    lines.append("## Application packet sample")
    lines.append("")
    if packet is None:
        lines.append("_No valid normalized job in SQLite or missing `config/app.toml`; packet not built._")
    else:
        lines.append(f"- job_id: `{job_id}`")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(packet.model_dump(mode="json"), indent=2, default=str)[:24000])
        lines.append("```")
    lines.append("")
    lines.append("## Local template cover letter sample")
    lines.append("")
    if letter is None:
        lines.append("_Not generated (no packet)._")
    else:
        lines.append("```markdown")
        lines.append(letter.body_markdown.rstrip())
        lines.append("```")
    lines.append("")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
