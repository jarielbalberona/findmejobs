from __future__ import annotations

from findmejobs.profile_bootstrap.models import (
    MissingFieldsReport,
    ProfileConfigDraft,
    ProfileExtractionPacket,
    ProfileRefinementPacket,
    RankingConfigDraft,
)

PROMPT_VERSION = "slice0.5-v1"

INSTRUCTIONS = """Extract only resume-backed profile data for findmejobs bootstrap.

Rules:
- Resume text is bootstrap input only, not final truth.
- Do not invent unsupported preferences.
- If a field is uncertain, leave it null or omit it and include it in low_confidence_fields.
- minimum_salary, require_remote, relocation_allowed, blocked_companies, and blocked_title_keywords must remain unset unless explicitly stated in the resume text.
- Include short evidence snippets for important extracted fields.
- Return structured JSON only.
"""

REFINEMENT_INSTRUCTIONS = """Refine an existing findmejobs draft using explicit user follow-up answers.

Rules:
- The resume draft remains bootstrap input only, not final truth.
- Update only fields directly supported by the latest user answers.
- Preserve unrelated fields.
- Do not invent unsupported preferences.
- minimum_salary, require_remote, relocation_allowed, blocked_companies, and blocked_title_keywords must remain unset unless the user explicitly states them.
- Keep uncertain fields in low_confidence_fields.
- Return structured JSON only.
"""


def build_extraction_packet(import_id: str, resume_text: str) -> ProfileExtractionPacket:
    return ProfileExtractionPacket(
        import_id=import_id,
        prompt_version=PROMPT_VERSION,
        instructions=INSTRUCTIONS,
        resume_text=resume_text,
        output_schema={
            "import_id": "string",
            "full_name": "string|null",
            "email": "string|null",
            "location_text": "string|null",
            "target_titles": ["string"],
            "required_skills": ["string"],
            "preferred_skills": ["string"],
            "preferred_locations": ["string"],
            "allowed_countries": ["string"],
            "minimum_salary": "integer|null",
            "require_remote": "boolean|null",
            "relocation_allowed": "boolean|null",
            "blocked_companies": ["string"],
            "blocked_title_keywords": ["string"],
            "evidence": {"field_name": ["short evidence"]},
            "low_confidence_fields": ["field_name"],
            "explicit_fields": ["field_name"],
        },
    )


def build_refinement_packet(
    import_id: str,
    profile_draft: ProfileConfigDraft,
    ranking_draft: RankingConfigDraft,
    missing_fields: MissingFieldsReport,
    user_answers: str,
) -> ProfileRefinementPacket:
    return ProfileRefinementPacket(
        import_id=import_id,
        prompt_version=PROMPT_VERSION,
        instructions=REFINEMENT_INSTRUCTIONS,
        current_profile_draft=profile_draft.model_dump(mode="json"),
        current_ranking_draft=ranking_draft.model_dump(mode="json"),
        missing_fields=missing_fields.model_dump(mode="json"),
        user_answers=user_answers,
        output_schema={
            "import_id": "string",
            "full_name": "string|null",
            "email": "string|null",
            "location_text": "string|null",
            "target_titles": ["string"],
            "required_skills": ["string"],
            "preferred_skills": ["string"],
            "preferred_locations": ["string"],
            "allowed_countries": ["string"],
            "minimum_salary": "integer|null",
            "require_remote": "boolean|null",
            "relocation_allowed": "boolean|null",
            "blocked_companies": ["string"],
            "blocked_title_keywords": ["string"],
            "evidence": {"field_name": ["short evidence"]},
            "low_confidence_fields": ["field_name"],
            "explicit_fields": ["field_name"],
        },
    )
