from __future__ import annotations

import json
from pathlib import Path

from findmejobs.profile_bootstrap.diff import compare_drafts
from findmejobs.profile_bootstrap.extractor import extract_resume, prepare_paths, snapshot_current_state
from findmejobs.profile_bootstrap.models import (
    DraftDiff,
    ImportMetadata,
    MissingFieldsReport,
    ProfileConfigDraft,
    RankingConfigDraft,
    ResumeExtractionDraft,
)
from findmejobs.profile_bootstrap.openclaw import FilesystemProfileBootstrapOpenClawClient
from findmejobs.profile_bootstrap.parser import build_profile_draft, build_ranking_draft, merge_extraction_drafts
from findmejobs.profile_bootstrap.promote import (
    load_existing_profile,
    load_existing_ranking,
    promote_drafts,
    snapshot_canonical_config,
)
from findmejobs.profile_bootstrap.prompts import build_extraction_packet, build_refinement_packet
from findmejobs.profile_bootstrap.validators import build_missing_fields_report, validate_drafts
from findmejobs.utils.ids import new_id
from findmejobs.utils.time import utcnow
from findmejobs.utils.yamlio import dump_yaml, load_yaml


class ProfileBootstrapService:
    def __init__(self, state_root: Path = Path("state/profile_bootstrap"), config_root: Path = Path("config"), *, id_factory=new_id):
        self.state_root = state_root
        self.config_root = config_root
        self.id_factory = id_factory

    def import_resume(
        self,
        *,
        file_path: Path | None,
        pasted_text: str | None,
        reimport: bool = False,
        refinement_answers: str | None = None,
    ) -> ImportMetadata:
        paths = prepare_paths(self.state_root, self.config_root)
        current_meta = self.load_import_metadata()
        if reimport and current_meta is not None:
            snapshot_current_state(paths, current_meta.import_id)
        import_id = self.id_factory()
        extracted_text, metadata = extract_resume(file_path=file_path, pasted_text=pasted_text, import_id=import_id, paths=paths)
        extraction = self._request_extraction(import_id, extracted_text, paths)
        if extraction is None:
            metadata.extraction_pending = True
            extraction = ResumeExtractionDraft()
        elif refinement_answers:
            refined = self._request_refinement(import_id, extraction, refinement_answers, paths)
            if refined is None:
                metadata.extraction_pending = True
                metadata.warnings.append("refinement_pending")
            else:
                extraction = merge_extraction_drafts(extraction, refined)
        metadata.low_confidence_fields = extraction.low_confidence_fields
        paths.extracted_meta_path.write_text(metadata.model_dump_json(indent=2), encoding="utf-8")
        profile_draft = build_profile_draft(extraction)
        ranking_draft = build_ranking_draft(extraction)
        missing = build_missing_fields_report(profile_draft, extraction.low_confidence_fields)
        self._write_drafts(profile_draft, ranking_draft, missing, metadata)
        if reimport:
            diff = self.diff_draft()
            dump_yaml(diff.model_dump(mode="json"), paths.diff_path)
        return metadata

    def refresh_pending_import(self, *, refinement_answers: str | None = None) -> ImportMetadata:
        paths = prepare_paths(self.state_root, self.config_root)
        metadata = self.load_import_metadata()
        if metadata is None:
            raise FileNotFoundError("no_pending_import_metadata")
        extraction = FilesystemProfileBootstrapOpenClawClient(paths.review_packet_path, paths.review_result_path).load_result(
            metadata.import_id
        )
        if extraction is None:
            raise RuntimeError("openclaw_extraction_result_pending")
        metadata.extraction_pending = False
        if refinement_answers:
            refined = self._request_refinement(metadata.import_id, extraction, refinement_answers, paths)
            if refined is None:
                metadata.extraction_pending = True
                metadata.warnings.append("refinement_pending")
            else:
                extraction = merge_extraction_drafts(extraction, refined)
        metadata.low_confidence_fields = extraction.low_confidence_fields
        paths.extracted_meta_path.write_text(metadata.model_dump_json(indent=2), encoding="utf-8")
        profile_draft = build_profile_draft(extraction)
        ranking_draft = build_ranking_draft(extraction)
        missing = build_missing_fields_report(profile_draft, extraction.low_confidence_fields)
        self._write_drafts(profile_draft, ranking_draft, missing, metadata)
        return metadata

    def _request_extraction(self, import_id: str, extracted_text: str, paths) -> ResumeExtractionDraft | None:
        packet = build_extraction_packet(import_id, extracted_text)
        client = FilesystemProfileBootstrapOpenClawClient(paths.review_packet_path, paths.review_result_path)
        client.export_request(packet)
        return client.load_result(import_id)

    def _request_refinement(
        self,
        import_id: str,
        extraction: ResumeExtractionDraft,
        refinement_answers: str,
        paths,
    ) -> ResumeExtractionDraft | None:
        profile_draft = build_profile_draft(extraction)
        ranking_draft = build_ranking_draft(extraction)
        missing = build_missing_fields_report(profile_draft, extraction.low_confidence_fields)
        packet = build_refinement_packet(import_id, profile_draft, ranking_draft, missing, refinement_answers)
        client = FilesystemProfileBootstrapOpenClawClient(paths.refinement_packet_path, paths.refinement_result_path)
        client.export_request(packet)
        return client.load_result(import_id)

    def load_import_metadata(self) -> ImportMetadata | None:
        paths = prepare_paths(self.state_root, self.config_root)
        if not paths.extracted_meta_path.exists():
            return None
        return ImportMetadata.model_validate(json.loads(paths.extracted_meta_path.read_text(encoding="utf-8")))

    def load_profile_draft(self) -> ProfileConfigDraft:
        paths = prepare_paths(self.state_root, self.config_root)
        return ProfileConfigDraft.model_validate(load_yaml(paths.profile_draft_path))

    def load_ranking_draft(self) -> RankingConfigDraft:
        paths = prepare_paths(self.state_root, self.config_root)
        return RankingConfigDraft.model_validate(load_yaml(paths.ranking_draft_path))

    def load_missing_fields(self) -> MissingFieldsReport:
        paths = prepare_paths(self.state_root, self.config_root)
        return MissingFieldsReport.model_validate(load_yaml(paths.missing_fields_path))

    def validate_draft(self) -> list[str]:
        profile = self.load_profile_draft()
        ranking = self.load_ranking_draft()
        missing = self.load_missing_fields()
        return validate_drafts(profile, ranking, missing)

    def diff_draft(self) -> DraftDiff:
        paths = prepare_paths(self.state_root, self.config_root)
        return compare_drafts(
            load_existing_profile(paths.canonical_profile_path),
            load_existing_ranking(paths.canonical_ranking_path),
            self.load_profile_draft(),
            self.load_ranking_draft(),
        )

    def promote_draft(self) -> DraftDiff:
        paths = prepare_paths(self.state_root, self.config_root)
        errors = self.validate_draft()
        if errors:
            raise ValueError(f"draft_validation_failed:{','.join(errors)}")
        import_id = self.load_import_metadata().import_id
        snapshot_canonical_config(
            paths.canonical_profile_path,
            paths.canonical_ranking_path,
            self.state_root / "promotions" / "snapshots" / import_id,
        )
        diff = promote_drafts(
            paths.canonical_profile_path,
            paths.canonical_ranking_path,
            self.load_profile_draft(),
            self.load_ranking_draft(),
        )
        provenance_path = self.state_root / "promotions" / f"{utcnow().strftime('%Y%m%d%H%M%S')}-{import_id}.md"
        provenance_path.parent.mkdir(parents=True, exist_ok=True)
        provenance_path.write_text(_promotion_report(diff), encoding="utf-8")
        return diff

    def _write_drafts(
        self,
        profile_draft: ProfileConfigDraft,
        ranking_draft: RankingConfigDraft,
        missing: MissingFieldsReport,
        metadata: ImportMetadata,
    ) -> None:
        paths = prepare_paths(self.state_root, self.config_root)
        dump_yaml(profile_draft.model_dump(mode="json"), paths.profile_draft_path)
        dump_yaml(ranking_draft.model_dump(mode="json"), paths.ranking_draft_path)
        dump_yaml(missing.model_dump(mode="json"), paths.missing_fields_path)
        paths.import_report_path.parent.mkdir(parents=True, exist_ok=True)
        paths.import_report_path.write_text(_build_import_report(metadata, missing), encoding="utf-8")


def _build_import_report(metadata: ImportMetadata, missing: MissingFieldsReport) -> str:
    lines = [
        f"# Resume Import Report",
        "",
        f"- import_id: `{metadata.import_id}`",
        f"- source_type: `{metadata.source_type}`",
        f"- original_filename: `{metadata.original_filename}`",
        f"- char_count: `{metadata.char_count}`",
        f"- extraction_pending: `{str(metadata.extraction_pending).lower()}`",
    ]
    if metadata.warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend([f"- {warning}" for warning in metadata.warnings])
    if metadata.detected_links:
        lines.extend(["", "## Detected Links", ""])
        lines.extend([f"- {link}" for link in metadata.detected_links])
    if missing.low_confidence_fields:
        lines.extend(["", "## Low Confidence Fields", ""])
        lines.extend([f"- {field}" for field in missing.low_confidence_fields])
    if missing.missing:
        lines.extend(["", "## Missing Fields", ""])
        for item in missing.missing:
            lines.append(f"- {item.field}: {item.reason} (required={str(item.required_for_promotion).lower()})")
    return "\n".join(lines) + "\n"


def _promotion_report(diff: DraftDiff) -> str:
    return "\n".join(
        [
            "# Profile Draft Promotion",
            "",
            "## Safe Auto Updates",
            *[f"- {field}" for field in diff.safe_auto_updates],
            "",
            "## Protected Conflicts",
            *[f"- {field}" for field in diff.protected_conflicts],
            "",
        ]
    ).strip() + "\n"
