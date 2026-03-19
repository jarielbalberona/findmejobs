from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from findmejobs.profile_bootstrap.models import ImportMetadata, ImportPaths
from findmejobs.utils.hashing import sha256_hexdigest
from findmejobs.utils.text import collapse_whitespace
from findmejobs.utils.time import utcnow


SUPPORTED_SUFFIXES = {".pdf", ".docx", ".txt", ".md", ".json"}
LINK_RE = re.compile(r"https?://[^\s<>()\"']+")


def prepare_paths(state_root: Path, config_root: Path) -> ImportPaths:
    return ImportPaths(
        state_root=state_root,
        input_path=state_root / "input" / "resume",
        extracted_text_path=state_root / "extracted" / "resume.txt",
        extracted_meta_path=state_root / "extracted" / "resume.meta.json",
        review_packet_path=state_root / "review" / "profile_extraction_packet.json",
        review_result_path=state_root / "review" / "openclaw_result.json",
        refinement_packet_path=state_root / "review" / "profile_refinement_packet.json",
        refinement_result_path=state_root / "review" / "openclaw_refinement_result.json",
        profile_draft_path=state_root / "drafts" / "profile.draft.yaml",
        ranking_draft_path=state_root / "drafts" / "ranking.draft.yaml",
        missing_fields_path=state_root / "drafts" / "missing_fields.yaml",
        import_report_path=state_root / "drafts" / "import_report.md",
        diff_path=state_root / "drafts" / "reimport_diff.yaml",
        canonical_profile_path=config_root / "profile.yaml",
        canonical_ranking_path=config_root / "ranking.yaml",
        history_root=state_root / "history",
    )


def extract_resume(
    *,
    file_path: Path | None,
    pasted_text: str | None,
    import_id: str,
    paths: ImportPaths,
) -> tuple[str, ImportMetadata]:
    if (file_path is None) == (pasted_text is None):
        raise ValueError("provide exactly one of file_path or pasted_text")
    if file_path is not None:
        stored_path = _persist_input_file(file_path, paths)
        source_type = file_path.suffix.casefold().lstrip(".")
        extracted_text, page_count, warnings = _extract_file_text(file_path)
        original_filename = file_path.name
        original_sha = sha256_hexdigest(file_path.read_bytes())
    else:
        stored_path = paths.state_root / "input" / "pasted.txt"
        stored_path.parent.mkdir(parents=True, exist_ok=True)
        stored_path.write_text(pasted_text or "", encoding="utf-8")
        source_type = "txt"
        extracted_text = collapse_whitespace(pasted_text or "")
        page_count = None
        warnings = []
        original_filename = "pasted.txt"
        original_sha = sha256_hexdigest(pasted_text or "")
    detected_links = _detect_links(extracted_text)
    paths.extracted_text_path.parent.mkdir(parents=True, exist_ok=True)
    paths.extracted_text_path.write_text(extracted_text, encoding="utf-8")
    metadata = ImportMetadata(
        import_id=import_id,
        source_type=source_type,
        original_filename=original_filename,
        stored_input_path=str(stored_path),
        extracted_text_path=str(paths.extracted_text_path),
        extracted_at=utcnow(),
        original_sha256=original_sha,
        extracted_text_sha256=sha256_hexdigest(extracted_text),
        char_count=len(extracted_text),
        page_count=page_count,
        warnings=warnings,
        detected_links=detected_links,
    )
    paths.extracted_meta_path.write_text(metadata.model_dump_json(indent=2), encoding="utf-8")
    return extracted_text, metadata


def snapshot_current_state(paths: ImportPaths, import_id: str) -> None:
    snapshot_root = paths.history_root / import_id
    if snapshot_root.exists():
        return
    for relative in ("input", "extracted", "drafts", "review"):
        source = paths.state_root / relative
        if source.exists():
            shutil.copytree(source, snapshot_root / relative)


def _persist_input_file(file_path: Path, paths: ImportPaths) -> Path:
    if not file_path.exists():
        raise FileNotFoundError(file_path)
    suffix = file_path.suffix.casefold()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(f"unsupported resume format: {suffix}")
    target = paths.state_root / "input" / file_path.name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(file_path.read_bytes())
    return target


def _extract_file_text(file_path: Path) -> tuple[str, int | None, list[str]]:
    suffix = file_path.suffix.casefold()
    if suffix == ".pdf":
        return _extract_pdf_text(file_path)
    if suffix == ".docx":
        return _extract_docx_text(file_path)
    if suffix in {".txt", ".md"}:
        text = collapse_whitespace(file_path.read_text(encoding="utf-8"))
        return text, None, []
    if suffix == ".json":
        return _extract_json_resume_text(file_path)
    raise ValueError(f"unsupported resume format: {suffix}")


def _extract_pdf_text(file_path: Path) -> tuple[str, int | None, list[str]]:
    try:
        from pypdf import PdfReader  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("pypdf is required for PDF resume import") from exc
    reader = PdfReader(str(file_path))
    pages = [collapse_whitespace(page.extract_text() or "") for page in reader.pages]
    text = collapse_whitespace(" ".join(page for page in pages if page))
    warnings: list[str] = []
    if not text:
        warnings.append("empty_pdf_text")
    return text, len(reader.pages), warnings


def _extract_docx_text(file_path: Path) -> tuple[str, int | None, list[str]]:
    try:
        from docx import Document  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("python-docx is required for DOCX resume import") from exc
    document = Document(str(file_path))
    text = collapse_whitespace(" ".join(paragraph.text for paragraph in document.paragraphs))
    warnings: list[str] = []
    if not text:
        warnings.append("empty_docx_text")
    return text, None, warnings


def _extract_json_resume_text(file_path: Path) -> tuple[str, int | None, list[str]]:
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("invalid_json_resume") from exc
    if not isinstance(payload, dict):
        raise ValueError("invalid_json_resume")

    lines: list[str] = []
    warnings: list[str] = []

    basics = payload.get("basics") if isinstance(payload.get("basics"), dict) else {}
    if basics:
        _append_kv(lines, "Name", basics.get("name"))
        _append_kv(lines, "Label", basics.get("label"))
        _append_kv(lines, "Email", basics.get("email"))
        _append_kv(lines, "Phone", basics.get("phone"))
        location = basics.get("location") if isinstance(basics.get("location"), dict) else {}
        if location:
            location_parts = [
                location.get("city"),
                location.get("region"),
                location.get("countryCode"),
            ]
            _append_kv(lines, "Location", ", ".join(str(part) for part in location_parts if part))
        _append_kv(lines, "Summary", basics.get("summary"))
        for profile in basics.get("profiles", []):
            if isinstance(profile, dict):
                _append_kv(lines, "Profile", " ".join(str(profile.get(key, "")) for key in ("network", "username", "url")).strip())

    for section_name, label in (
        ("work", "Work"),
        ("education", "Education"),
        ("projects", "Project"),
        ("volunteer", "Volunteer"),
        ("publications", "Publication"),
    ):
        items = payload.get(section_name, [])
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            values = [str(item.get(key, "")) for key in ("name", "position", "studyType", "area", "summary", "website", "url")]
            line = collapse_whitespace(" ".join(value for value in values if value))
            if line:
                lines.append(f"{label}: {line}")
            for highlight in item.get("highlights", []):
                cleaned = collapse_whitespace(str(highlight))
                if cleaned:
                    lines.append(f"{label} highlight: {cleaned}")

    skills = payload.get("skills", [])
    if not isinstance(skills, list):
        skills = []
    for skill in skills:
        if not isinstance(skill, dict):
            continue
        skill_parts = [str(skill.get("name", "")), str(skill.get("level", ""))]
        skill_line = collapse_whitespace(" ".join(part for part in skill_parts if part))
        if skill_line:
            lines.append(f"Skill: {skill_line}")
        for keyword in skill.get("keywords", []):
            cleaned = collapse_whitespace(str(keyword))
            if cleaned:
                lines.append(f"Skill keyword: {cleaned}")

    text = collapse_whitespace(" ".join(lines))
    if not text:
        warnings.append("empty_json_resume_text")
    return text, None, warnings


def _append_kv(lines: list[str], key: str, value: object) -> None:
    cleaned = collapse_whitespace(str(value or ""))
    if cleaned:
        lines.append(f"{key}: {cleaned}")


def _detect_links(text: str) -> list[str]:
    links: list[str] = []
    seen: set[str] = set()
    for match in LINK_RE.findall(text):
        if match in seen:
            continue
        seen.add(match)
        links.append(match)
    return links
