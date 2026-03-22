from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re
import shutil

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from findmejobs.application.models import (
    ApplicationArtifactMetadata,
    AnswerDraftResultModel,
    AnswerDraftSetModel,
    ApplicationAnswerDraftModel,
    ApplicationCanonicalJobSummary,
    ApplicationMatchedProfileSummary,
    ApplicationMissingInput,
    ApplicationPacketModel,
    ApplicationQuestionModel,
    ApplicationReviewSummary,
    ApplicationScoreSummary,
    ApplicationSourceSummary,
    ApplicationValidationReport,
    CoverLetterDraftModel,
    CoverLetterDraftResultModel,
)
from findmejobs.application.openclaw import FilesystemApplicationDraftOpenClawClient
from findmejobs.application.prompts import (
    ANSWER_PROMPT_VERSION,
    COVER_LETTER_PROMPT_VERSION,
    build_answer_request,
    build_cover_letter_request,
)
from findmejobs.config.models import ProfileConfig
from findmejobs.db.models import JobCluster, JobClusterMember, JobScore, NormalizedJob, OpenClawReview, Profile, RankModel, ReviewPacket, Source, SourceJob
from findmejobs.review.packets import build_review_packet, sanitize_review_text
from findmejobs.utils.hashing import sha256_hexdigest
from findmejobs.utils.text import collapse_whitespace, truncate_text
from findmejobs.utils.time import utcnow
from findmejobs.utils.yamlio import dump_yaml, load_yaml

APPLICATION_PACKET_FILENAME = "application_packet.json"
COVER_LETTER_FILENAME = "cover_letter.draft.md"
COVER_LETTER_META_FILENAME = "cover_letter.meta.json"
ANSWERS_FILENAME = "answers.draft.yaml"
ANSWERS_META_FILENAME = "answers.meta.json"
MISSING_INPUTS_FILENAME = "missing_inputs.yaml"
DRAFT_REPORT_FILENAME = "draft_report.md"

GENERIC_PLACEHOLDER = "User input required before final submission."
USER_INPUT_REQUIRED_TOKEN = "user input required"
UNSUPPORTED_EXPERIENCE_RE = re.compile(r"\b\d+\+?\s+years?\b", re.IGNORECASE)
QUESTION_KEYS = {
    "expected_salary": ("salary_expectation", "Expected salary depends on explicit user input."),
    "notice_period": ("notice_period", "Notice period is not present in the canonical profile."),
    "current_availability": ("current_availability", "Current availability is not present in the canonical profile."),
    "relocation_preference": ("relocation_preference", "Relocation preference is not present in the canonical profile."),
    "work_authorization": ("work_authorization", "Work authorization is not present in the canonical profile."),
    "work_hours": ("work_hours", "Specific hours or timezone willingness is not present in the canonical profile."),
    "remote_preference": ("remote_preference", "Remote preference is not present in the canonical profile."),
    "cover_letter_signature": ("full_name", "Cover letter signature needs the operator's name."),
}
RESPONSIBILITY_MARKERS = (
    "build",
    "design",
    "develop",
    "maintain",
    "own",
    "lead",
    "deliver",
    "implement",
    "optimize",
    "operate",
    "support",
    "collaborate",
)
REQUIREMENT_MARKERS = (
    "must",
    "required",
    "qualification",
    "experience with",
    "proficient",
    "knowledge of",
    "familiar with",
    "nice to have",
    "preferred",
)
DOMAIN_MARKERS = (
    "platform",
    "product",
    "api",
    "customer",
    "data",
    "cloud",
    "fintech",
    "healthcare",
    "ecommerce",
    "saas",
    "payments",
)


@dataclass(slots=True)
class ApplicationPaths:
    job_root: Path
    openclaw_dir: Path
    history_root: Path
    packet_path: Path
    cover_letter_path: Path
    cover_letter_meta_path: Path
    answers_path: Path
    answers_meta_path: Path
    missing_inputs_path: Path
    draft_report_path: Path


@dataclass(slots=True)
class ApplicationJobContext:
    job: NormalizedJob
    cluster: JobCluster
    source: Source
    source_job: SourceJob
    score: JobScore
    review_packet: ReviewPacket | None
    review: OpenClawReview | None


class ApplicationDraftService:
    def __init__(self, state_root: Path = Path("state/applications")) -> None:
        self.state_root = state_root

    def prepare_application(
        self,
        session: Session,
        profile: ProfileConfig,
        *,
        job_id: str,
        questions_file: Path | None = None,
        snapshot_existing: bool = True,
    ) -> tuple[ApplicationPacketModel, list[ApplicationMissingInput]]:
        context = self._load_job_context(session, profile, job_id)
        questions = self._collect_questions(context.source_job, questions_file)
        packet, missing_inputs = self._build_application_packet(context, profile, questions)
        paths = self._paths(job_id)
        if snapshot_existing:
            self._snapshot_existing(paths)
        request_client = FilesystemApplicationDraftOpenClawClient(paths.openclaw_dir)
        self._write_json(paths.packet_path, packet.model_dump(mode="json"))
        self._write_yaml(paths.missing_inputs_path, [item.model_dump(mode="json") for item in missing_inputs])
        request_client.export_cover_letter_request(build_cover_letter_request(packet))
        request_client.export_answers_request(build_answer_request(packet))
        self._write_report(paths, packet, missing_inputs, cover_letter=None, answers=None)
        return packet, missing_inputs

    def readiness_from_packet(
        self,
        *,
        packet: ApplicationPacketModel,
        missing_inputs: list[ApplicationMissingInput],
    ) -> tuple[str, list[str], list[str]]:
        _ = packet
        categories = sorted({item.key for item in missing_inputs})
        if categories:
            blockers = [f"missing_input:{item}" for item in categories]
            return "needs_input", blockers, categories
        return "ready", [], []

    def draft_cover_letter(
        self,
        session: Session,
        profile: ProfileConfig,
        *,
        job_id: str,
        questions_file: Path | None = None,
        snapshot_existing: bool = True,
    ) -> CoverLetterDraftModel:
        packet, missing_inputs = self.prepare_application(
            session,
            profile,
            job_id=job_id,
            questions_file=questions_file,
            snapshot_existing=snapshot_existing,
        )
        paths = self._paths(job_id)
        client = FilesystemApplicationDraftOpenClawClient(paths.openclaw_dir)
        imported = client.load_cover_letter_result()
        if imported is not None:
            self._validate_imported_cover_letter(imported, packet, missing_inputs)
            draft = CoverLetterDraftModel(
                job_id=job_id,
                company_name=packet.company_name,
                role_title=packet.role_title,
                origin="openclaw",
                prompt_version=imported.prompt_version,
                body_markdown=imported.body_markdown.strip() + "\n",
                missing_inputs=sorted(set(imported.missing_inputs)),
                created_at=utcnow(),
            )
        else:
            draft = self._build_local_cover_letter(packet, missing_inputs)
        self._write_text(paths.cover_letter_path, draft.body_markdown)
        self._write_artifact_metadata(
            paths.cover_letter_meta_path,
            ApplicationArtifactMetadata(
                artifact_type="cover_letter",
                job_id=job_id,
                origin=draft.origin,
                prompt_version=draft.prompt_version,
                packet_sha256=self._packet_sha(packet),
                created_at=draft.created_at,
                missing_input_keys=draft.missing_inputs,
            ),
        )
        self._write_report(paths, packet, missing_inputs, cover_letter=draft, answers=self._load_answers(paths))
        return draft

    def draft_answers(
        self,
        session: Session,
        profile: ProfileConfig,
        *,
        job_id: str,
        questions_file: Path | None = None,
        snapshot_existing: bool = True,
    ) -> AnswerDraftSetModel:
        packet, missing_inputs = self.prepare_application(
            session,
            profile,
            job_id=job_id,
            questions_file=questions_file,
            snapshot_existing=snapshot_existing,
        )
        paths = self._paths(job_id)
        client = FilesystemApplicationDraftOpenClawClient(paths.openclaw_dir)
        imported = client.load_answers_result()
        if imported is not None:
            answers = AnswerDraftSetModel(
                job_id=job_id,
                origin="openclaw",
                prompt_version=imported.prompt_version,
                answers=imported.answers,
                missing_inputs=imported.missing_inputs,
                created_at=utcnow(),
            )
            self._validate_answer_payload(packet, missing_inputs, answers)
        else:
            answers = self._build_local_answers(packet, profile, missing_inputs)
        self._write_yaml(paths.answers_path, answers.model_dump(mode="json"))
        self._write_artifact_metadata(
            paths.answers_meta_path,
            ApplicationArtifactMetadata(
                artifact_type="answers",
                job_id=job_id,
                origin=answers.origin,
                prompt_version=answers.prompt_version,
                packet_sha256=self._packet_sha(packet),
                created_at=answers.created_at,
                missing_input_keys=[item.key for item in answers.missing_inputs],
                answer_count=len(answers.answers),
            ),
        )
        self._write_report(paths, packet, missing_inputs, cover_letter=self._load_cover_letter(paths), answers=answers)
        return answers

    def regenerate_application(
        self,
        session: Session,
        profile: ProfileConfig,
        *,
        job_id: str,
        questions_file: Path | None = None,
    ) -> dict[str, object]:
        self._snapshot_existing(self._paths(job_id))
        packet, missing_inputs = self.prepare_application(
            session,
            profile,
            job_id=job_id,
            questions_file=questions_file,
            snapshot_existing=False,
        )
        cover_letter = self.draft_cover_letter(
            session,
            profile,
            job_id=job_id,
            questions_file=questions_file,
            snapshot_existing=False,
        )
        answers = self.draft_answers(
            session,
            profile,
            job_id=job_id,
            questions_file=questions_file,
            snapshot_existing=False,
        )
        return {
            "job_id": job_id,
            "packet_version": packet.packet_version,
            "missing_inputs": [item.model_dump(mode="json") for item in missing_inputs],
            "cover_letter_origin": cover_letter.origin,
            "answers_origin": answers.origin,
        }

    def show_application(self, *, job_id: str) -> dict[str, object]:
        paths = self._paths(job_id)
        if not paths.packet_path.exists():
            raise FileNotFoundError(f"application_not_prepared:{job_id}")
        payload: dict[str, object] = {
            "job_id": job_id,
            "application_packet": json.loads(paths.packet_path.read_text(encoding="utf-8")),
            "missing_inputs": load_yaml(paths.missing_inputs_path) if paths.missing_inputs_path.exists() else [],
            "cover_letter": paths.cover_letter_path.read_text(encoding="utf-8") if paths.cover_letter_path.exists() else None,
            "cover_letter_meta": self._read_json(paths.cover_letter_meta_path),
            "answers": load_yaml(paths.answers_path) if paths.answers_path.exists() else None,
            "answers_meta": self._read_json(paths.answers_meta_path),
            "draft_report": paths.draft_report_path.read_text(encoding="utf-8") if paths.draft_report_path.exists() else None,
        }
        return payload

    def validate_application(
        self,
        session: Session,
        profile: ProfileConfig,
        *,
        job_id: str,
    ) -> ApplicationValidationReport:
        errors: list[str] = []
        warnings: list[str] = []
        try:
            self._load_job_context(session, profile, job_id)
        except ValueError as exc:
            return ApplicationValidationReport(
                job_id=job_id,
                eligible=False,
                readiness_state="ineligible",
                blockers=[str(exc)],
                errors=[str(exc)],
            )

        paths = self._paths(job_id)
        if not paths.packet_path.exists():
            errors.append("application_packet_missing")
            return ApplicationValidationReport(
                job_id=job_id,
                eligible=True,
                readiness_state="needs_input",
                blockers=errors.copy(),
                errors=errors,
                warnings=warnings,
            )

        try:
            packet = ApplicationPacketModel.model_validate(json.loads(paths.packet_path.read_text(encoding="utf-8")))
        except (ValidationError, json.JSONDecodeError) as exc:
            errors.append(f"application_packet_invalid:{exc}")
            return ApplicationValidationReport(
                job_id=job_id,
                eligible=True,
                readiness_state="needs_input",
                blockers=errors.copy(),
                packet_prepared=True,
                errors=errors,
                warnings=warnings,
            )

        packet_sha = self._packet_sha(packet)
        if paths.missing_inputs_path.exists():
            try:
                missing_inputs = [ApplicationMissingInput.model_validate(item) for item in load_yaml(paths.missing_inputs_path)]
            except (ValidationError, ValueError) as exc:
                errors.append(f"missing_inputs_invalid:{exc}")
                missing_inputs = []
            if missing_inputs:
                warnings.extend(sorted({f"missing_input:{item.key}" for item in missing_inputs}))
        else:
            errors.append("missing_inputs_file_missing")
            missing_inputs = []

        try:
            build_cover_letter_request(packet)
            build_answer_request(packet)
        except ValueError as exc:
            errors.append(f"draft_request_invalid:{exc}")

        if (paths.openclaw_dir / "cover_letter.request.json").exists():
            from findmejobs.application.models import CoverLetterDraftRequestModel

            try:
                CoverLetterDraftRequestModel.model_validate(json.loads((paths.openclaw_dir / "cover_letter.request.json").read_text(encoding="utf-8")))
            except (ValidationError, json.JSONDecodeError) as exc:
                errors.append(f"cover_letter_request_invalid:{exc}")
        else:
            errors.append("cover_letter_request_missing")
        if (paths.openclaw_dir / "answers.request.json").exists():
            from findmejobs.application.models import AnswerDraftRequestModel

            try:
                AnswerDraftRequestModel.model_validate(json.loads((paths.openclaw_dir / "answers.request.json").read_text(encoding="utf-8")))
            except (ValidationError, json.JSONDecodeError) as exc:
                errors.append(f"answers_request_invalid:{exc}")
        else:
            errors.append("answers_request_missing")

        cover_letter_status = self._validate_cover_letter_state(paths, packet, packet_sha, missing_inputs, errors)
        answers_status = self._validate_answers_state(paths, packet, packet_sha, missing_inputs, errors)
        readiness_state, readiness_blockers, missing_categories = self.readiness_from_packet(
            packet=packet,
            missing_inputs=missing_inputs,
        )
        blockers = list(dict.fromkeys([*readiness_blockers, *errors]))
        if cover_letter_status != "current":
            blockers.append(f"cover_letter_status:{cover_letter_status}")
        if answers_status != "current":
            blockers.append(f"answers_status:{answers_status}")
        if not blockers and readiness_state != "ready":
            blockers = readiness_blockers
        if blockers and readiness_state == "ready":
            readiness_state = "needs_input"
        return ApplicationValidationReport(
            job_id=job_id,
            eligible=True,
            readiness_state=readiness_state,
            blockers=blockers,
            missing_input_categories=missing_categories,
            complete=not errors and cover_letter_status == "current" and answers_status == "current",
            packet_prepared=True,
            packet_sha256=packet_sha,
            cover_letter_status=cover_letter_status,
            answers_status=answers_status,
            errors=errors,
            warnings=warnings,
        )

    def _paths(self, job_id: str) -> ApplicationPaths:
        job_root = self.state_root / job_id
        return ApplicationPaths(
            job_root=job_root,
            openclaw_dir=job_root / "openclaw",
            history_root=job_root / "history",
            packet_path=job_root / APPLICATION_PACKET_FILENAME,
            cover_letter_path=job_root / COVER_LETTER_FILENAME,
            cover_letter_meta_path=job_root / COVER_LETTER_META_FILENAME,
            answers_path=job_root / ANSWERS_FILENAME,
            answers_meta_path=job_root / ANSWERS_META_FILENAME,
            missing_inputs_path=job_root / MISSING_INPUTS_FILENAME,
            draft_report_path=job_root / DRAFT_REPORT_FILENAME,
        )

    def _load_job_context(self, session: Session, profile: ProfileConfig, job_id: str) -> ApplicationJobContext:
        stmt = (
            select(NormalizedJob, JobCluster, Source, SourceJob, JobScore, ReviewPacket, OpenClawReview)
            .join(JobClusterMember, JobClusterMember.normalized_job_id == NormalizedJob.id)
            .join(JobCluster, JobCluster.id == JobClusterMember.cluster_id)
            .join(SourceJob, SourceJob.id == NormalizedJob.source_job_id)
            .join(Source, Source.id == SourceJob.source_id)
            .join(JobScore, JobScore.cluster_id == JobCluster.id)
            .join(Profile, Profile.id == JobScore.profile_id)
            .join(RankModel, RankModel.id == JobScore.rank_model_id)
            .outerjoin(ReviewPacket, (ReviewPacket.cluster_id == JobCluster.id) & (ReviewPacket.job_score_id == JobScore.id))
            .outerjoin(OpenClawReview, OpenClawReview.review_packet_id == ReviewPacket.id)
            .where(NormalizedJob.id == job_id)
            .where(NormalizedJob.normalization_status == "valid")
            .where(JobScore.passed_hard_filters.is_(True))
            .where(JobScore.score_total >= profile.ranking.minimum_score)
            .where(Profile.version == profile.version)
            .where(RankModel.version == profile.rank_model_version)
            .order_by(JobScore.scored_at.desc())
        )
        row = session.execute(stmt).first()
        if row is None:
            raise ValueError(f"job_not_eligible:{job_id}")
        job, cluster, source, source_job, score, review_packet, review = row
        return ApplicationJobContext(
            job=job,
            cluster=cluster,
            source=source,
            source_job=source_job,
            score=score,
            review_packet=review_packet,
            review=review,
        )

    def _build_application_packet(
        self,
        context: ApplicationJobContext,
        profile: ProfileConfig,
        questions: list[ApplicationQuestionModel],
    ) -> tuple[ApplicationPacketModel, list[ApplicationMissingInput]]:
        job = context.job
        sanitized_excerpt = truncate_text(sanitize_review_text(job.description_text), 1800)
        job_text = " ".join([job.title, sanitized_excerpt, " ".join(job.tags_json or [])]).casefold()
        matched_required = [skill for skill in profile.required_skills if skill.casefold() in job_text]
        missing_required = [skill for skill in profile.required_skills if skill.casefold() not in job_text]
        matched_preferred = [skill for skill in profile.preferred_skills if skill.casefold() in job_text]
        review_packet = context.review_packet
        review_model = (
            build_review_packet(
                packet_id=review_packet.id if review_packet is not None else f"application-review-{job.id}",
                cluster_id=context.cluster.id,
                job=self._canonical_job(context),
                total_score=context.score.score_total,
                score_breakdown=context.score.score_breakdown_json,
            )
            if review_packet is None
            else build_review_packet(
                packet_id=review_packet.id,
                cluster_id=context.cluster.id,
                job=self._canonical_job(context),
                total_score=context.score.score_total,
                score_breakdown=context.score.score_breakdown_json,
            )
        )
        summary_lines = [
            f"Target titles: {', '.join(profile.target_titles[:3])}" if profile.target_titles else "Target titles not specified.",
            f"Matched required skills: {', '.join(matched_required)}" if matched_required else "No required skill match was detected directly in the sanitized job text.",
        ]
        if matched_preferred:
            summary_lines.append(f"Matched preferred skills: {', '.join(matched_preferred[:4])}")
        if profile.location_text:
            summary_lines.append(f"Profile location: {profile.location_text}")
        elif profile.preferred_locations:
            summary_lines.append(f"Preferred locations: {', '.join(profile.preferred_locations[:3])}")
        strengths = self._build_strengths(profile, matched_required, matched_preferred)
        detected_gaps = []
        if missing_required:
            detected_gaps.append(f"Required skills not obvious in the sanitized job text: {', '.join(missing_required[:4])}")
        if job.employment_type is None:
            detected_gaps.append("Employment type is not specified in normalized job data.")
        unknowns = []
        if job.salary_min is None and job.salary_max is None:
            unknowns.append("Compensation is not disclosed.")
        if job.seniority is None:
            unknowns.append("Seniority is not disclosed.")
        if not sanitized_excerpt:
            unknowns.append("Sanitized job description excerpt is empty.")
        score_summary = ApplicationScoreSummary(
            total=round(context.score.score_total, 2),
            breakdown=context.score.score_breakdown_json,
            breakdown_summary=self._summarize_score(context.score.score_breakdown_json),
            matched_signals=[key for key, value in context.score.score_breakdown_json.items() if isinstance(value, (int, float)) and value > 0],
        )
        packet = ApplicationPacketModel(
            job_id=job.id,
            cluster_id=context.cluster.id,
            company_name=job.company_name,
            role_title=job.title,
            source=ApplicationSourceSummary(
                source_id=context.source.id,
                source_name=context.source.name,
                source_kind=context.source.kind,
                source_job_key=context.source_job.source_job_key,
                source_url=context.source_job.source_url,
                apply_url=context.source_job.apply_url,
                trust_weight=context.source.trust_weight,
                priority=context.source.priority,
            ),
            canonical_job=ApplicationCanonicalJobSummary(
                company_name=job.company_name,
                role_title=job.title,
                location_text=job.location_text,
                location_type=job.location_type,
                country_code=job.country_code,
                city=job.city,
                region=job.region,
                seniority=job.seniority,
                employment_type=job.employment_type,
                salary_summary=self._salary_summary(job),
                posted_at=job.posted_at,
                canonical_url=job.canonical_url,
                description_excerpt=sanitized_excerpt,
                tags=job.tags_json,
            ),
            score=score_summary,
            review_summary=ApplicationReviewSummary(
                packet_version=review_model.packet_version,
                review_status="reviewed" if context.review is not None else "eligible",
                description_excerpt=review_model.description_excerpt,
                matched_signals=review_model.matched_signals,
                decision=context.review.decision if context.review is not None else None,
                reasons=context.review.reasons_json if context.review is not None else [],
                draft_summary=context.review.draft_summary if context.review is not None else None,
            ),
            matched_profile=ApplicationMatchedProfileSummary(
                profile_version=profile.version,
                full_name=profile.full_name,
                email=profile.email,
                location_text=profile.location_text,
                target_titles=profile.target_titles,
                matched_required_skills=matched_required,
                missing_required_skills=missing_required,
                matched_preferred_skills=matched_preferred,
                summary_lines=summary_lines,
            ),
            relevant_strengths=strengths,
            detected_gaps=detected_gaps,
            unknowns=unknowns,
            application_questions=questions,
            safe_context=self._safe_context(
                job,
                context.source.name,
                sanitized_excerpt=sanitized_excerpt,
                profile=profile,
                matched_required=matched_required,
                matched_preferred=matched_preferred,
            ),
        )
        missing_inputs = self._detect_missing_inputs(packet, profile)
        return packet, missing_inputs

    def _canonical_job(self, context: ApplicationJobContext):
        from findmejobs.domain.job import CanonicalJob

        return CanonicalJob(
            source_job_id=context.job.source_job_id,
            source_id=context.source.id,
            source_job_key=context.source_job.source_job_key,
            source_name=context.source.name,
            source_trust_weight=context.source.trust_weight,
            source_priority=context.source.priority,
            canonical_url=context.job.canonical_url,
            company_name=context.job.company_name,
            title=context.job.title,
            location_text=context.job.location_text,
            location_type=context.job.location_type,
            country_code=context.job.country_code,
            city=context.job.city,
            region=context.job.region,
            seniority=context.job.seniority,
            employment_type=context.job.employment_type,
            salary_min=context.job.salary_min,
            salary_max=context.job.salary_max,
            salary_currency=context.job.salary_currency,
            salary_period=context.job.salary_period,
            description_text=context.job.description_text,
            tags=context.job.tags_json,
            posted_at=context.job.posted_at,
            first_seen_at=context.job.first_seen_at,
            last_seen_at=context.job.last_seen_at,
            normalization_errors=context.job.normalization_errors_json,
        )

    def _collect_questions(self, source_job: SourceJob, questions_file: Path | None) -> list[ApplicationQuestionModel]:
        collected: list[ApplicationQuestionModel] = []
        seen_prompts: set[str] = set()
        for question in self._questions_from_payload(source_job.payload_json):
            if question.prompt.casefold() in seen_prompts:
                continue
            seen_prompts.add(question.prompt.casefold())
            collected.append(question)
        for question in self._questions_from_file(questions_file):
            if question.prompt.casefold() in seen_prompts:
                continue
            seen_prompts.add(question.prompt.casefold())
            collected.append(question)
        return collected

    def _questions_from_payload(self, payload: dict) -> list[ApplicationQuestionModel]:
        candidates = []
        for key in ("application_questions", "questions", "screening_questions"):
            value = payload.get(key)
            if value:
                candidates.extend(self._normalize_question_items(value, source=key))
        return candidates

    def _questions_from_file(self, questions_file: Path | None) -> list[ApplicationQuestionModel]:
        if questions_file is None:
            return []
        if questions_file.suffix.casefold() in {".yaml", ".yml", ".json"}:
            try:
                return self._normalize_question_items(load_yaml(questions_file), source="questions_file", strict=True)
            except Exception as exc:
                if questions_file.suffix.casefold() in {".yaml", ".yml"}:
                    text = questions_file.read_text(encoding="utf-8")
                    bullet_lines = [
                        collapse_whitespace(line.removeprefix("- ").strip())
                        for line in text.splitlines()
                        if line.strip()
                    ]
                    if bullet_lines and all(not line.startswith("{") for line in bullet_lines):
                        return self._normalize_question_items(bullet_lines, source="questions_file", strict=True)
                raise ValueError(f"invalid_questions_file:{questions_file}:{exc}") from exc
        lines = [collapse_whitespace(line) for line in questions_file.read_text(encoding="utf-8").splitlines()]
        return self._normalize_question_items([line for line in lines if line], source="questions_file")

    def _normalize_question_items(self, value: object, *, source: str, strict: bool = False) -> list[ApplicationQuestionModel]:
        if isinstance(value, dict):
            if "questions" in value:
                raw_items = value.get("questions")
            elif any(key in value for key in ("prompt", "question", "text")):
                raw_items = [value]
            elif strict:
                raise ValueError("questions file object must contain 'questions' or prompt-like fields")
            else:
                raw_items = [value]
        elif isinstance(value, list):
            raw_items = value
        elif strict and not isinstance(value, str):
            raise ValueError("questions file must be a list, object, or string list")
        else:
            raw_items = [value]
        questions: list[ApplicationQuestionModel] = []
        for idx, item in enumerate(raw_items, start=1):
            if isinstance(item, dict):
                prompt = str(item.get("prompt") or item.get("question") or item.get("text") or "").strip()
                if not prompt:
                    if strict:
                        raise ValueError(f"questions file item {idx} is missing prompt text")
                    continue
                normalized_key, response_type = self._classify_question(prompt)
                questions.append(
                    ApplicationQuestionModel(
                        question_id=str(item.get("question_id") or item.get("id") or f"{source}-{idx}"),
                        prompt=prompt,
                        source=source,
                        response_type=str(item.get("response_type") or response_type or "text"),
                        required=bool(item.get("required", False)),
                        normalized_key=normalized_key,
                        options=item.get("options") or [],
                    )
                )
                continue
            if strict and not isinstance(item, str):
                raise ValueError(f"questions file item {idx} must be a string or object")
            prompt = str(item).strip()
            if not prompt:
                if strict:
                    raise ValueError(f"questions file item {idx} is empty")
                continue
            normalized_key, response_type = self._classify_question(prompt)
            questions.append(
                ApplicationQuestionModel(
                    question_id=f"{source}-{idx}",
                    prompt=prompt,
                    source=source,
                    response_type=response_type,
                    normalized_key=normalized_key,
                )
            )
        return questions

    def _classify_question(self, prompt: str) -> tuple[str | None, str | None]:
        lowered = prompt.casefold()
        if "salary" in lowered or "compensation" in lowered:
            return "expected_salary", "text"
        if "notice period" in lowered or "how soon can you start" in lowered:
            return "notice_period", "text"
        if "availability" in lowered or "available to start" in lowered:
            return "current_availability", "text"
        if "relocat" in lowered:
            return "relocation_preference", "text"
        if "visa" in lowered or "work authorization" in lowered or "authorised to work" in lowered:
            return "work_authorization", "text"
        if "time zone" in lowered or "timezone" in lowered or "work hours" in lowered or "shift" in lowered or " hours" in lowered:
            return "work_hours", "text"
        if "remote" in lowered:
            return "remote_preference", "text"
        if "why are you a fit" in lowered or "why should we hire" in lowered or "fit for this role" in lowered:
            return "fit", "text"
        if "why do you want" in lowered or "why this role" in lowered or "why are you interested" in lowered:
            return "motivation", "text"
        if "project" in lowered or "experience" in lowered:
            return "project_experience", "text"
        return None, "text"

    def _build_strengths(self, profile: ProfileConfig, matched_required: list[str], matched_preferred: list[str]) -> list[str]:
        strengths: list[str] = []
        if profile.application.professional_summary:
            strengths.append(profile.application.professional_summary)
        if matched_required:
            strengths.append(f"Relevant core skills: {', '.join(matched_required[:4])}.")
        if matched_preferred:
            strengths.append(f"Additional aligned skills: {', '.join(matched_preferred[:4])}.")
        if profile.target_titles:
            strengths.append(f"Target role focus includes {', '.join(profile.target_titles[:3])}.")
        strengths.extend(profile.application.key_achievements[:2])
        strengths.extend(profile.application.project_highlights[:2])
        if not strengths:
            strengths.append("Profile contains target-role alignment and a constrained skills list, but no richer application summary.")
        return strengths[:6]

    def _summarize_score(self, breakdown: dict[str, float]) -> list[str]:
        positive = [(key, value) for key, value in breakdown.items() if isinstance(value, (int, float)) and value > 0]
        positive.sort(key=lambda item: item[1], reverse=True)
        return [f"{key.replace('_', ' ')}: {value:.2f}" for key, value in positive[:4]]

    def _safe_context(
        self,
        job: NormalizedJob,
        source_name: str,
        *,
        sanitized_excerpt: str,
        profile: ProfileConfig,
        matched_required: list[str],
        matched_preferred: list[str],
    ) -> list[str]:
        context = [
            f"Role: {job.title} at {job.company_name}",
            f"Source: {source_name}",
            f"Location: {job.location_text or 'Unknown'}",
            f"Employment type: {job.employment_type or 'Not specified'}",
            f"Seniority: {job.seniority or 'Not specified'}",
            f"Compensation: {self._salary_summary(job) or 'Not disclosed'}",
        ]
        if job.tags_json:
            context.append(f"Tags: {', '.join(job.tags_json[:6])}")
        parsed_context = self._extract_job_description_context(
            sanitized_excerpt,
            profile=profile,
            matched_required=matched_required,
            matched_preferred=matched_preferred,
            tags=job.tags_json or [],
        )
        if parsed_context["responsibilities"]:
            context.append(f"Responsibilities cues: {' | '.join(parsed_context['responsibilities'])}")
        if parsed_context["requirements"]:
            context.append(f"Requirements cues: {' | '.join(parsed_context['requirements'])}")
        if parsed_context["stack"]:
            context.append(f"Stack and domain cues: {', '.join(parsed_context['stack'])}")
        return context

    def _extract_job_description_context(
        self,
        sanitized_excerpt: str,
        *,
        profile: ProfileConfig,
        matched_required: list[str],
        matched_preferred: list[str],
        tags: list[str],
    ) -> dict[str, list[str]]:
        sentences = self._split_context_sentences(sanitized_excerpt)
        responsibilities = self._select_sentences_by_markers(sentences, RESPONSIBILITY_MARKERS, limit=2)
        requirements = self._select_sentences_by_markers(sentences, REQUIREMENT_MARKERS, limit=2)
        stack = self._stack_cues(
            sanitized_excerpt,
            profile=profile,
            matched_required=matched_required,
            matched_preferred=matched_preferred,
            tags=tags,
        )
        return {
            "responsibilities": responsibilities,
            "requirements": requirements,
            "stack": stack,
        }

    def _split_context_sentences(self, sanitized_excerpt: str) -> list[str]:
        if not sanitized_excerpt:
            return []
        parts = re.split(r"(?<=[.!?])\s+", sanitized_excerpt)
        out: list[str] = []
        for part in parts:
            cleaned = collapse_whitespace(part).strip(" -•\t")
            if len(cleaned) < 24:
                continue
            out.append(cleaned)
        return out[:24]

    def _select_sentences_by_markers(
        self,
        sentences: list[str],
        markers: tuple[str, ...],
        *,
        limit: int,
    ) -> list[str]:
        selected: list[str] = []
        for sentence in sentences:
            lowered = sentence.casefold()
            if not any(marker in lowered for marker in markers):
                continue
            selected.append(truncate_text(sentence, 180))
            if len(selected) >= limit:
                break
        return selected

    def _stack_cues(
        self,
        sanitized_excerpt: str,
        *,
        profile: ProfileConfig,
        matched_required: list[str],
        matched_preferred: list[str],
        tags: list[str],
    ) -> list[str]:
        lowered_text = sanitized_excerpt.casefold()
        cues: list[str] = []
        seen: set[str] = set()
        for skill in matched_required + matched_preferred:
            key = skill.casefold()
            if key in seen:
                continue
            seen.add(key)
            cues.append(skill)
        for tag in tags:
            key = tag.casefold()
            if key in seen or key not in lowered_text:
                continue
            seen.add(key)
            cues.append(tag)
        for skill in profile.required_skills + profile.preferred_skills:
            key = skill.casefold()
            if key in seen or key not in lowered_text:
                continue
            seen.add(key)
            cues.append(skill)
        for marker in DOMAIN_MARKERS:
            if marker in lowered_text and marker not in seen:
                seen.add(marker)
                cues.append(marker)
        return cues[:10]

    def _salary_summary(self, job: NormalizedJob) -> str | None:
        if job.salary_min is None and job.salary_max is None:
            return None
        numbers = []
        if job.salary_min is not None:
            numbers.append(str(job.salary_min))
        if job.salary_max is not None and job.salary_max != job.salary_min:
            numbers.append(str(job.salary_max))
        currency = job.salary_currency or "USD"
        period = job.salary_period or "year"
        return f"{currency} {'-'.join(numbers)} / {period}"

    def _detect_missing_inputs(self, packet: ApplicationPacketModel, profile: ProfileConfig) -> list[ApplicationMissingInput]:
        missing: dict[str, ApplicationMissingInput] = {}
        if not profile.full_name:
            key, reason = QUESTION_KEYS["cover_letter_signature"]
            missing[key] = ApplicationMissingInput(key=key, reason=reason, required_for=["cover_letter"])
        application = profile.application
        for question in packet.application_questions:
            normalized_key = question.normalized_key
            if normalized_key in {"fit", "motivation", "project_experience", None}:
                domain_missing = self._domain_gap(question, profile, packet)
                if domain_missing is not None:
                    existing = missing.get(domain_missing.key)
                    if existing is None:
                        missing[domain_missing.key] = domain_missing
                    else:
                        existing.questions = sorted(set(existing.questions + domain_missing.questions))
                        existing.required_for = sorted(set(existing.required_for + domain_missing.required_for))
                continue
            field_name, reason = QUESTION_KEYS[normalized_key]
            value = getattr(application, field_name, None)
            if field_name == "remote_preference":
                value = application.remote_preference or ("remote" if profile.ranking.require_remote else None)
            if value:
                continue
            record = missing.setdefault(
                field_name,
                ApplicationMissingInput(key=field_name, reason=reason, required_for=["answers"], questions=[]),
            )
            record.questions.append(question.prompt)
        return sorted(missing.values(), key=lambda item: item.key)

    def _domain_gap(
        self,
        question: ApplicationQuestionModel,
        profile: ProfileConfig,
        packet: ApplicationPacketModel,
    ) -> ApplicationMissingInput | None:
        if question.normalized_key not in {None, "project_experience"}:
            return None
        lowered = question.prompt.casefold()
        if "salary" in lowered or "notice" in lowered:
            return None
        tokens = {
            token.strip(" ,.?/()")
            for token in lowered.split()
            if len(token.strip(" ,.?/()")) >= 4 and token not in {"what", "your", "this", "that", "with", "have", "role", "work", "experience", "project"}
        }
        if not tokens:
            return None
        profile_text = " ".join(
            profile.required_skills
            + profile.preferred_skills
            + profile.target_titles
            + profile.application.key_achievements
            + profile.application.project_highlights
            + ([profile.application.professional_summary] if profile.application.professional_summary else [])
        ).casefold()
        if any(token in profile_text for token in tokens):
            return None
        return ApplicationMissingInput(
            key="domain_specific_experience",
            reason="Question asks for domain-specific experience that is not supported by the canonical profile.",
            questions=[question.prompt],
            required_for=["answers"],
        )

    def _build_local_cover_letter(
        self,
        packet: ApplicationPacketModel,
        missing_inputs: list[ApplicationMissingInput],
    ) -> CoverLetterDraftModel:
        intro_strength = packet.relevant_strengths[0].rstrip(".")
        second_strength = packet.relevant_strengths[1].rstrip(".") if len(packet.relevant_strengths) > 1 else intro_strength
        lines = [
            "Dear Hiring Team,",
            "",
            (
                f"I am applying for the {packet.role_title} role at {packet.company_name}. "
                f"The opportunity aligns with my current focus on {', '.join(packet.matched_profile.target_titles[:2]) or packet.role_title.lower()} "
                f"and the profile strengths captured in this application packet."
            ),
            "",
            (
                f"My background is grounded in {intro_strength}. "
                f"The job's sanitized requirements and score signals also align with {second_strength.lower()}."
            ),
            "",
            "I would value the chance to discuss how that background can support the team.",
            "",
            "Regards,",
        ]
        missing_keys = sorted({item.key for item in missing_inputs if "cover_letter" in item.required_for})
        if packet.matched_profile.full_name:
            lines.append(packet.matched_profile.full_name)
        body = "\n".join(lines).strip() + "\n"
        return CoverLetterDraftModel(
            job_id=packet.job_id,
            company_name=packet.company_name,
            role_title=packet.role_title,
            origin="local_template",
            prompt_version=COVER_LETTER_PROMPT_VERSION,
            body_markdown=body,
            missing_inputs=missing_keys,
            created_at=utcnow(),
        )

    def _build_local_answers(
        self,
        packet: ApplicationPacketModel,
        profile: ProfileConfig,
        missing_inputs: list[ApplicationMissingInput],
    ) -> AnswerDraftSetModel:
        missing_by_key = {item.key: item for item in missing_inputs}
        answers: list[ApplicationAnswerDraftModel] = []
        for question in packet.application_questions:
            normalized_key = question.normalized_key
            answer_text = ""
            answer_missing: list[str] = []
            needs_user_input = False
            if normalized_key == "fit":
                answer_text = self._fit_answer(packet)
            elif normalized_key == "motivation":
                answer_text = self._motivation_answer(packet)
            elif normalized_key == "project_experience":
                gap = missing_by_key.get("domain_specific_experience")
                if gap and question.prompt in gap.questions:
                    answer_text = GENERIC_PLACEHOLDER
                    answer_missing = [gap.key]
                    needs_user_input = True
                else:
                    answer_text = self._project_answer(profile, packet)
            elif normalized_key in QUESTION_KEYS:
                field_name, _reason = QUESTION_KEYS[normalized_key]
                if field_name == "remote_preference":
                    value = profile.application.remote_preference or ("Remote-first." if profile.ranking.require_remote else None)
                else:
                    value = getattr(profile.application, field_name, None)
                if value:
                    answer_text = value
                else:
                    answer_text = GENERIC_PLACEHOLDER
                    answer_missing = [field_name]
                    needs_user_input = True
            else:
                gap = missing_by_key.get("domain_specific_experience")
                if gap and question.prompt in gap.questions:
                    answer_text = GENERIC_PLACEHOLDER
                    answer_missing = [gap.key]
                    needs_user_input = True
                else:
                    answer_text = self._fit_answer(packet)
            answers.append(
                ApplicationAnswerDraftModel(
                    question_id=question.question_id,
                    question=question.prompt,
                    normalized_key=normalized_key,
                    answer=answer_text,
                    needs_user_input=needs_user_input,
                    missing_inputs=answer_missing,
                )
            )
        return AnswerDraftSetModel(
            job_id=packet.job_id,
            origin="local_template",
            prompt_version=ANSWER_PROMPT_VERSION,
            answers=answers,
            missing_inputs=missing_inputs,
            created_at=utcnow(),
        )

    def _fit_answer(self, packet: ApplicationPacketModel) -> str:
        strengths = packet.relevant_strengths[:2]
        return collapse_whitespace(
            f"My profile aligns with this role through {strengths[0].lower()} "
            f"and {strengths[1].lower() if len(strengths) > 1 else 'a direct match to the role focus'}. "
            f"The ranked packet also shows clear alignment on {', '.join(packet.score.matched_signals[:2]).replace('_', ' ')}."
        )

    def _motivation_answer(self, packet: ApplicationPacketModel) -> str:
        return collapse_whitespace(
            f"This {packet.role_title} role is a strong match for the work I am targeting, "
            f"especially around {', '.join(packet.matched_profile.matched_required_skills[:3]) or 'the core responsibilities in the posting'}. "
            f"I am interested because the role description and operating context align with that focus."
        )

    def _project_answer(self, profile: ProfileConfig, packet: ApplicationPacketModel) -> str:
        if profile.application.project_highlights:
            return profile.application.project_highlights[0]
        if profile.application.professional_summary:
            return profile.application.professional_summary
        return collapse_whitespace(
            f"My recent work is centered on {', '.join(packet.matched_profile.target_titles[:2]) or packet.role_title.lower()} "
            f"with practical use of {', '.join(packet.matched_profile.matched_required_skills[:3]) or ', '.join(profile.required_skills[:3])}."
        )

    def _snapshot_existing(self, paths: ApplicationPaths) -> None:
        existing = [path for path in self._known_files(paths) if path.exists()]
        if not existing:
            return
        stamp = utcnow().strftime("%Y%m%d%H%M%S")
        destination = paths.history_root / stamp
        destination.mkdir(parents=True, exist_ok=True)
        for path in existing:
            shutil.copy2(path, destination / path.name)

    def _known_files(self, paths: ApplicationPaths) -> list[Path]:
        return [
            paths.packet_path,
            paths.cover_letter_path,
            paths.cover_letter_meta_path,
            paths.answers_path,
            paths.answers_meta_path,
            paths.missing_inputs_path,
            paths.draft_report_path,
            paths.openclaw_dir / "cover_letter.request.json",
            paths.openclaw_dir / "cover_letter.result.json",
            paths.openclaw_dir / "answers.request.json",
            paths.openclaw_dir / "answers.result.json",
        ]

    def _write_json(self, path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    def _read_json(self, path: Path) -> dict[str, object] | None:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_yaml(self, path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        dump_yaml(payload, path)

    def _write_text(self, path: Path, payload: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")

    def _load_cover_letter(self, paths: ApplicationPaths) -> CoverLetterDraftModel | None:
        if not paths.cover_letter_path.exists() or not paths.packet_path.exists():
            return None
        packet = ApplicationPacketModel.model_validate(json.loads(paths.packet_path.read_text(encoding="utf-8")))
        metadata = self._load_artifact_metadata(paths.cover_letter_meta_path)
        return CoverLetterDraftModel(
            job_id=packet.job_id,
            company_name=packet.company_name,
            role_title=packet.role_title,
            origin=metadata.origin if metadata is not None else "stored",
            prompt_version=metadata.prompt_version if metadata is not None else COVER_LETTER_PROMPT_VERSION,
            body_markdown=paths.cover_letter_path.read_text(encoding="utf-8"),
            missing_inputs=metadata.missing_input_keys if metadata is not None else [],
            created_at=metadata.created_at if metadata is not None else utcnow(),
        )

    def _load_answers(self, paths: ApplicationPaths) -> AnswerDraftSetModel | None:
        if not paths.answers_path.exists():
            return None
        return AnswerDraftSetModel.model_validate(load_yaml(paths.answers_path))

    def _write_artifact_metadata(self, path: Path, metadata: ApplicationArtifactMetadata) -> None:
        self._write_json(path, metadata.model_dump(mode="json"))

    def _load_artifact_metadata(self, path: Path) -> ApplicationArtifactMetadata | None:
        if not path.exists():
            return None
        return ApplicationArtifactMetadata.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def _packet_sha(self, packet: ApplicationPacketModel) -> str:
        payload = json.dumps(packet.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return sha256_hexdigest(payload)

    def _cover_letter_missing_keys(self, missing_inputs: list[ApplicationMissingInput]) -> list[str]:
        return sorted({item.key for item in missing_inputs if "cover_letter" in item.required_for})

    def _missing_input_keys(self, missing_inputs: list[ApplicationMissingInput]) -> set[str]:
        return {item.key for item in missing_inputs}

    def _question_map(self, packet: ApplicationPacketModel) -> dict[str, ApplicationQuestionModel]:
        return {question.question_id: question for question in packet.application_questions}

    def _bounded_packet_text(self, packet: ApplicationPacketModel) -> str:
        values = [
            packet.company_name,
            packet.role_title,
            packet.canonical_job.description_excerpt,
            *packet.relevant_strengths,
            *packet.detected_gaps,
            *packet.unknowns,
            *packet.safe_context,
            *packet.score.breakdown_summary,
            *packet.score.matched_signals,
            *packet.matched_profile.target_titles,
            *packet.matched_profile.matched_required_skills,
            *packet.matched_profile.matched_preferred_skills,
            *packet.matched_profile.summary_lines,
        ]
        return " ".join(values).casefold()

    def _assert_no_unsupported_experience_claim(self, text: str, packet: ApplicationPacketModel, *, field_name: str) -> None:
        allowed_text = self._bounded_packet_text(packet)
        for match in UNSUPPORTED_EXPERIENCE_RE.findall(text):
            if match.casefold() not in allowed_text:
                raise ValueError(f"{field_name}_contains_unsupported_experience_claim")

    def _validate_imported_cover_letter(
        self,
        imported: CoverLetterDraftResultModel,
        packet: ApplicationPacketModel,
        missing_inputs: list[ApplicationMissingInput],
    ) -> None:
        required_missing_keys = set(self._cover_letter_missing_keys(missing_inputs))
        imported_missing_keys = set(imported.missing_inputs)
        unexpected_missing = sorted(imported_missing_keys - required_missing_keys)
        if unexpected_missing:
            raise ValueError(f"cover_letter_missing_inputs_unapproved:{','.join(unexpected_missing)}")
        missing_required = sorted(required_missing_keys - imported_missing_keys)
        if missing_required:
            raise ValueError(f"cover_letter_missing_inputs_incomplete:{','.join(missing_required)}")
        if packet.company_name.casefold() not in imported.body_markdown.casefold():
            raise ValueError("cover_letter_missing_company_name")
        if packet.role_title.casefold() not in imported.body_markdown.casefold():
            raise ValueError("cover_letter_missing_role_title")
        if len(imported.body_markdown.split()) > 180:
            raise ValueError("cover_letter_too_long")
        self._assert_no_unsupported_experience_claim(imported.body_markdown, packet, field_name="cover_letter")

    def _required_missing_key_for_question(
        self,
        question: ApplicationQuestionModel,
        missing_by_key: dict[str, ApplicationMissingInput],
    ) -> str | None:
        if question.normalized_key in QUESTION_KEYS:
            field_name, _reason = QUESTION_KEYS[question.normalized_key]
            if field_name in missing_by_key:
                return field_name
        gap = missing_by_key.get("domain_specific_experience")
        if gap is not None and question.prompt in gap.questions:
            return gap.key
        return None

    def _validate_answer_payload(
        self,
        packet: ApplicationPacketModel,
        missing_inputs: list[ApplicationMissingInput],
        payload: AnswerDraftSetModel,
    ) -> None:
        allowed_missing_keys = self._missing_input_keys(missing_inputs)
        payload_missing_keys = {item.key for item in payload.missing_inputs}
        unexpected_missing_keys = sorted(payload_missing_keys - allowed_missing_keys)
        if unexpected_missing_keys:
            raise ValueError(f"answers_missing_inputs_unapproved:{','.join(unexpected_missing_keys)}")

        question_map = self._question_map(packet)
        answer_ids = {answer.question_id for answer in payload.answers}
        missing_answer_ids = sorted(set(question_map) - answer_ids)
        unexpected_answer_ids = sorted(answer_ids - set(question_map))
        if missing_answer_ids:
            raise ValueError(f"answers_missing_questions:{','.join(missing_answer_ids)}")
        if unexpected_answer_ids:
            raise ValueError(f"answers_unapproved_questions:{','.join(unexpected_answer_ids)}")

        missing_by_key = {item.key: item for item in missing_inputs}
        for answer in payload.answers:
            question = question_map[answer.question_id]
            if answer.question != question.prompt:
                raise ValueError(f"answers_question_text_mismatch:{answer.question_id}")
            if answer.normalized_key != question.normalized_key:
                raise ValueError(f"answers_normalized_key_mismatch:{answer.question_id}")
            required_missing_key = self._required_missing_key_for_question(question, missing_by_key)
            if required_missing_key is not None:
                if not answer.needs_user_input:
                    raise ValueError(f"answers_missing_user_input_flag:{answer.question_id}")
                if required_missing_key not in answer.missing_inputs:
                    raise ValueError(f"answers_missing_input_key_mismatch:{answer.question_id}:{required_missing_key}")
                if USER_INPUT_REQUIRED_TOKEN not in answer.answer.casefold():
                    raise ValueError(f"answers_placeholder_missing:{answer.question_id}")
                continue
            if answer.needs_user_input:
                raise ValueError(f"answers_unexpected_user_input_flag:{answer.question_id}")
            self._assert_no_unsupported_experience_claim(answer.answer, packet, field_name=f"answer:{answer.question_id}")

    def _validate_cover_letter_state(
        self,
        paths: ApplicationPaths,
        packet: ApplicationPacketModel,
        packet_sha: str,
        missing_inputs: list[ApplicationMissingInput],
        errors: list[str],
    ) -> str:
        if not paths.cover_letter_path.exists():
            errors.append("cover_letter_missing")
            return "missing"
        try:
            metadata = self._load_artifact_metadata(paths.cover_letter_meta_path)
        except (ValidationError, json.JSONDecodeError) as exc:
            errors.append(f"cover_letter_metadata_invalid:{exc}")
            return "invalid"
        if metadata is None:
            errors.append("cover_letter_metadata_missing")
            return "invalid"
        if metadata.artifact_type != "cover_letter":
            errors.append("cover_letter_metadata_type_invalid")
            return "invalid"
        if metadata.job_id != packet.job_id:
            errors.append("cover_letter_metadata_job_mismatch")
            return "invalid"
        if metadata.packet_sha256 != packet_sha:
            errors.append("cover_letter_stale")
            return "stale"
        try:
            draft = CoverLetterDraftModel(
                job_id=packet.job_id,
                company_name=packet.company_name,
                role_title=packet.role_title,
                origin=metadata.origin,
                prompt_version=metadata.prompt_version,
                body_markdown=paths.cover_letter_path.read_text(encoding="utf-8"),
                missing_inputs=metadata.missing_input_keys,
                created_at=metadata.created_at,
            )
            self._validate_imported_cover_letter(
                CoverLetterDraftResultModel(
                    prompt_version=draft.prompt_version,
                    body_markdown=draft.body_markdown,
                    missing_inputs=draft.missing_inputs,
                    raw_response={},
                ),
                packet,
                missing_inputs,
            )
        except (ValidationError, ValueError) as exc:
            errors.append(f"cover_letter_invalid:{exc}")
            return "invalid"
        return "current"

    def _validate_answers_state(
        self,
        paths: ApplicationPaths,
        packet: ApplicationPacketModel,
        packet_sha: str,
        missing_inputs: list[ApplicationMissingInput],
        errors: list[str],
    ) -> str:
        if not paths.answers_path.exists():
            errors.append("answers_draft_missing")
            return "missing"
        try:
            metadata = self._load_artifact_metadata(paths.answers_meta_path)
        except (ValidationError, json.JSONDecodeError) as exc:
            errors.append(f"answers_metadata_invalid:{exc}")
            return "invalid"
        if metadata is None:
            errors.append("answers_metadata_missing")
            return "invalid"
        if metadata.artifact_type != "answers":
            errors.append("answers_metadata_type_invalid")
            return "invalid"
        if metadata.job_id != packet.job_id:
            errors.append("answers_metadata_job_mismatch")
            return "invalid"
        if metadata.packet_sha256 != packet_sha:
            errors.append("answers_stale")
            return "stale"
        try:
            payload = AnswerDraftSetModel.model_validate(load_yaml(paths.answers_path))
            if metadata.answer_count != len(payload.answers):
                raise ValueError("answers_metadata_count_mismatch")
            if sorted(metadata.missing_input_keys) != sorted(item.key for item in payload.missing_inputs):
                raise ValueError("answers_metadata_missing_inputs_mismatch")
            self._validate_answer_payload(packet, missing_inputs, payload)
        except (ValidationError, ValueError) as exc:
            errors.append(str(exc))
            return "invalid"
        return "current"

    def _write_report(
        self,
        paths: ApplicationPaths,
        packet: ApplicationPacketModel,
        missing_inputs: list[ApplicationMissingInput],
        *,
        cover_letter: CoverLetterDraftModel | None,
        answers: AnswerDraftSetModel | None,
    ) -> None:
        history_entries = len(list(paths.history_root.glob("*"))) if paths.history_root.exists() else 0
        lines = [
            "# Application Draft Report",
            "",
            f"- job_id: `{packet.job_id}`",
            f"- packet_sha256: `{self._packet_sha(packet)}`",
            f"- company: `{packet.company_name}`",
            f"- role_title: `{packet.role_title}`",
            f"- score_total: `{packet.score.total:.2f}`",
            f"- source: `{packet.source.source_name}`",
            f"- review_status: `{packet.review_summary.review_status}`",
            f"- cover_letter_origin: `{cover_letter.origin if cover_letter else 'not_generated'}`",
            f"- answers_origin: `{answers.origin if answers else 'not_generated'}`",
            f"- history_snapshots: `{history_entries}`",
            "",
            "## Relevant Strengths",
            *[f"- {item}" for item in packet.relevant_strengths],
            "",
            "## Missing Inputs",
        ]
        if missing_inputs:
            lines.extend([f"- {item.key}: {item.reason}" for item in missing_inputs])
        else:
            lines.append("- none")
        if packet.application_questions:
            lines.extend(["", "## Application Questions"])
            lines.extend([f"- {item.prompt}" for item in packet.application_questions])
        self._write_text(paths.draft_report_path, "\n".join(lines).rstrip() + "\n")
