from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import ValidationError

from findmejobs.profile_bootstrap.baseline import build_baseline_extraction
from findmejobs.profile_bootstrap.diff import compare_drafts
from findmejobs.profile_bootstrap.extractor import extract_resume, prepare_paths, snapshot_current_state
from findmejobs.profile_bootstrap.models import (
    DraftDiff,
    DraftValidationResult,
    ImportMetadata,
    MissingFieldsReport,
    ProfileConfigDraft,
    RankingConfigDraft,
    ResumeExtractionDraft,
)
from findmejobs.profile_bootstrap.openclaw import FilesystemProfileBootstrapOpenClawClient, parse_openclaw_result
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

LOGGER = logging.getLogger(__name__)


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
        self._log_stage(
            "resume_extracted",
            import_id=import_id,
            stored_input_path=metadata.stored_input_path,
            extracted_text_path=metadata.extracted_text_path,
            char_count=metadata.char_count,
        )
        extraction = build_baseline_extraction(import_id, extracted_text)
        self._log_stage("baseline_draft_built", import_id=import_id, target_titles=len(extraction.target_titles), skills=len(extraction.required_skills) + len(extraction.preferred_skills))
        model_extraction = self._request_extraction(import_id, extracted_text, extraction, paths)
        if model_extraction is None:
            metadata.extraction_pending = True
            self._write_raw_response(paths.raw_draft_response_path, extraction.model_dump_json(indent=2))
            self._log_stage("raw_draft_response_saved", import_id=import_id, source="deterministic_baseline", path=str(paths.raw_draft_response_path))
        else:
            extraction = merge_extraction_drafts(extraction, model_extraction)
            self._log_stage("structured_draft_parsed", import_id=import_id, source="openclaw")
        if refinement_answers:
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
        validation = validate_drafts(profile_draft, ranking_draft, missing, source_char_count=metadata.char_count)
        if validation.status == "failed":
            raise RuntimeError(f"draft_generation_failed:{','.join(validation.errors)}")
        self._write_drafts(profile_draft, ranking_draft, missing, metadata, validation)
        if reimport:
            diff = self.diff_draft()
            dump_yaml(diff.model_dump(mode="json"), paths.diff_path)
        self._log_stage("draft_artifacts_written", import_id=import_id, profile_path=str(paths.profile_draft_path), ranking_path=str(paths.ranking_draft_path))
        self._log_stage("draft_validation_complete", import_id=import_id, status=validation.status, errors=validation.errors)
        return metadata

    def refresh_pending_import(self, *, refinement_answers: str | None = None) -> ImportMetadata:
        paths = prepare_paths(self.state_root, self.config_root)
        metadata = self.load_import_metadata()
        if metadata is None:
            raise FileNotFoundError("no_pending_import_metadata")
        extracted_text = paths.extracted_text_path.read_text(encoding="utf-8")
        extraction = build_baseline_extraction(metadata.import_id, extracted_text)
        self._log_stage("baseline_draft_built", import_id=metadata.import_id, target_titles=len(extraction.target_titles), skills=len(extraction.required_skills) + len(extraction.preferred_skills))
        model_extraction = self._load_raw_result(
            FilesystemProfileBootstrapOpenClawClient(paths.review_packet_path, paths.review_result_path),
            metadata.import_id,
            paths.raw_draft_response_path,
        )
        if model_extraction is None:
            raise RuntimeError("openclaw_extraction_result_pending")
        extraction = merge_extraction_drafts(extraction, model_extraction)
        self._log_stage("structured_draft_parsed", import_id=metadata.import_id, source="openclaw")
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
        validation = validate_drafts(profile_draft, ranking_draft, missing, source_char_count=metadata.char_count)
        if validation.status == "failed":
            raise RuntimeError(f"draft_generation_failed:{','.join(validation.errors)}")
        self._write_drafts(profile_draft, ranking_draft, missing, metadata, validation)
        self._log_stage("draft_artifacts_written", import_id=metadata.import_id, profile_path=str(paths.profile_draft_path), ranking_path=str(paths.ranking_draft_path))
        self._log_stage("draft_validation_complete", import_id=metadata.import_id, status=validation.status, errors=validation.errors)
        return metadata

    def _request_extraction(
        self,
        import_id: str,
        extracted_text: str,
        baseline_extraction: ResumeExtractionDraft,
        paths,
    ) -> ResumeExtractionDraft | None:
        baseline_profile = build_profile_draft(baseline_extraction)
        baseline_ranking = build_ranking_draft(baseline_extraction)
        baseline_missing = build_missing_fields_report(baseline_profile, baseline_extraction.low_confidence_fields)
        packet = build_extraction_packet(import_id, extracted_text, baseline_profile, baseline_ranking, baseline_missing)
        client = FilesystemProfileBootstrapOpenClawClient(paths.review_packet_path, paths.review_result_path)
        client.export_request(packet)
        self._log_stage("draft_request_exported", import_id=import_id, request_path=str(paths.review_packet_path))
        return self._load_raw_result(client, import_id, paths.raw_draft_response_path)

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
        self._log_stage("refinement_request_exported", import_id=import_id, request_path=str(paths.refinement_packet_path))
        result = self._load_raw_result(client, import_id, paths.raw_draft_response_path)
        if result is not None:
            self._log_stage("structured_draft_parsed", import_id=import_id, source="openclaw_refinement")
        return result

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

    def validate_draft(self) -> DraftValidationResult:
        profile = self.load_profile_draft()
        ranking = self.load_ranking_draft()
        missing = self.load_missing_fields()
        metadata = self.load_import_metadata()
        source_char_count = metadata.char_count if metadata is not None else None
        return validate_drafts(profile, ranking, missing, source_char_count=source_char_count)

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
        validation = self.validate_draft()
        if validation.errors:
            raise ValueError(f"draft_validation_failed:{','.join(validation.errors)}")
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
        validation: DraftValidationResult,
    ) -> None:
        paths = prepare_paths(self.state_root, self.config_root)
        dump_yaml(profile_draft.model_dump(mode="json"), paths.profile_draft_path)
        dump_yaml(ranking_draft.model_dump(mode="json"), paths.ranking_draft_path)
        dump_yaml(missing.model_dump(mode="json"), paths.missing_fields_path)
        paths.import_report_path.parent.mkdir(parents=True, exist_ok=True)
        paths.import_report_path.write_text(_build_import_report(metadata, profile_draft, ranking_draft, missing, validation), encoding="utf-8")

    def _load_raw_result(
        self,
        client: FilesystemProfileBootstrapOpenClawClient,
        import_id: str,
        raw_response_path: Path,
    ) -> ResumeExtractionDraft | None:
        raw_text = client.load_result_text()
        if raw_text is None:
            return None
        self._write_raw_response(raw_response_path, raw_text)
        self._log_stage("raw_draft_response_saved", import_id=import_id, source="openclaw", path=str(raw_response_path))
        try:
            return parse_openclaw_result(raw_text, import_id)
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            raise RuntimeError(f"draft_generation_result_invalid:{exc}") from exc

    def _write_raw_response(self, path: Path, raw_text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(raw_text.rstrip() + "\n", encoding="utf-8")

    def _log_stage(self, message: str, **payload: object) -> None:
        LOGGER.info(message, extra={"payload": payload})


def _build_import_report(
    metadata: ImportMetadata,
    profile: ProfileConfigDraft,
    ranking: RankingConfigDraft,
    missing: MissingFieldsReport,
    validation: DraftValidationResult,
) -> str:
    lines = [
        f"# Resume Import Report",
        "",
        f"- import_id: `{metadata.import_id}`",
        f"- source_type: `{metadata.source_type}`",
        f"- original_filename: `{metadata.original_filename}`",
        f"- char_count: `{metadata.char_count}`",
        f"- extraction_pending: `{str(metadata.extraction_pending).lower()}`",
        f"- validation_status: `{validation.status}`",
    ]
    if profile.full_name or profile.headline:
        lines.extend(["", "## Extracted Profile", ""])
        if profile.full_name:
            lines.append(f"- full_name: {profile.full_name}")
        if profile.headline:
            lines.append(f"- headline: {profile.headline}")
        if profile.location_text:
            lines.append(f"- location_text: {profile.location_text}")
        if profile.years_experience is not None:
            lines.append(f"- years_experience: {profile.years_experience}")
        if profile.email:
            lines.append(f"- email: {profile.email}")
        if profile.phone:
            lines.append(f"- phone: {profile.phone}")
        if profile.github_url:
            lines.append(f"- github_url: {profile.github_url}")
        if profile.linkedin_url:
            lines.append(f"- linkedin_url: {profile.linkedin_url}")
    if profile.target_titles or profile.required_skills or profile.preferred_skills:
        lines.extend(["", "## Draft Signals", ""])
        if profile.target_titles:
            lines.append(f"- target_titles: {', '.join(profile.target_titles)}")
        if profile.required_skills:
            lines.append(f"- required_skills: {', '.join(profile.required_skills)}")
        if profile.preferred_skills:
            lines.append(f"- preferred_skills: {', '.join(profile.preferred_skills)}")
        if profile.strengths:
            lines.append(f"- strengths: {', '.join(profile.strengths)}")
        if ranking.title_families:
            lines.append(f"- title_families: {json.dumps(ranking.title_families, ensure_ascii=True)}")
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
    if validation.errors:
        lines.extend(["", "## Validation Errors", ""])
        lines.extend([f"- {error}" for error in validation.errors])
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
