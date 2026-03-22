from __future__ import annotations

import json
from pathlib import Path

import pytest

from findmejobs.config.loader import load_profile_config
from findmejobs.profile_bootstrap.extractor import _extract_file_text, prepare_paths
from findmejobs.profile_bootstrap.models import ResumeExtractionDraft
from findmejobs.profile_bootstrap.service import ProfileBootstrapService
from findmejobs.utils.yamlio import load_yaml


class PendingClient:
    def __init__(self, request_path: Path, result_path: Path) -> None:
        self.request_path = request_path
        self.result_path = result_path

    def export_request(self, packet) -> Path:
        self.request_path.parent.mkdir(parents=True, exist_ok=True)
        self.request_path.write_text(packet.model_dump_json(indent=2), encoding="utf-8")
        return self.request_path

    def load_result_text(self):
        return None

    def _expected_import_id(self) -> str:
        return json.loads(self.request_path.read_text(encoding="utf-8"))["import_id"]


class ResultClient(PendingClient):
    def load_result_text(self):
        return ResumeExtractionDraft(
            import_id=self._expected_import_id(),
            full_name="Jane Doe",
            headline="Senior Backend Engineer",
            email="jane@example.com",
            phone="+63 917 000 0000",
            location_text="Manila, Philippines",
            github_url="https://github.com/janedoe",
            linkedin_url="https://linkedin.com/in/janedoe",
            years_experience=7,
            summary="Senior Backend Engineer with 7 years building Python, SQL, FastAPI, and AWS systems.",
            strengths=["Python", "SQL", "FastAPI", "AWS"],
            recent_titles=["Senior Backend Engineer"],
            recent_companies=["Example Co"],
            target_titles=["Backend Engineer", "Platform Engineer"],
            required_skills=["Python", "SQL"],
            preferred_skills=["FastAPI", "AWS"],
            preferred_locations=["Remote", "Philippines"],
            allowed_countries=["PH", "SG"],
            evidence={"target_titles": ["Target roles: Backend Engineer, Platform Engineer"]},
            low_confidence_fields=["allowed_countries"],
            explicit_fields=[],
        ).model_dump_json(indent=2)


class HardPreferenceClient(PendingClient):
    def load_result_text(self):
        return ResumeExtractionDraft(
            import_id=self._expected_import_id(),
            full_name="Jane Doe",
            target_titles=["Backend Engineer"],
            required_skills=["Python"],
            preferred_locations=["Remote"],
            minimum_salary=250000,
            require_remote=True,
            blocked_companies=["Bad Co"],
            explicit_fields=["minimum_salary", "require_remote", "blocked_companies"],
        ).model_dump_json(indent=2)


class RefinementClient(PendingClient):
    def load_result_text(self):
        if self.request_path.name == "profile_refinement_packet.json":
            return ResumeExtractionDraft(
                import_id=self._expected_import_id(),
                preferred_skills=["AWS", "FastAPI"],
                preferred_locations=["Remote"],
                low_confidence_fields=["preferred_locations"],
            ).model_dump_json(indent=2)
        return ResumeExtractionDraft(
            import_id=self._expected_import_id(),
            full_name="Jane Doe",
            email="jane@example.com",
            location_text="Manila, Philippines",
            target_titles=["Backend Engineer"],
            required_skills=["Python", "SQL"],
            explicit_fields=[],
        ).model_dump_json(indent=2)


class MalformedClient(PendingClient):
    def load_result_text(self):
        return '{"import_id": "bad",'


@pytest.fixture()
def bootstrap_paths(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "state" / "profile_bootstrap", tmp_path / "config"


def test_txt_and_markdown_extraction_work(fixtures_dir: Path) -> None:
    txt, _, _ = _extract_file_text(fixtures_dir / "resume.txt")
    md, _, _ = _extract_file_text(fixtures_dir / "resume.md")
    assert "Jane Doe" in txt
    assert "Backend Engineer" in md


def test_json_resume_extraction_work(fixtures_dir: Path) -> None:
    text, _, warnings = _extract_file_text(fixtures_dir / "resume.json")
    assert "Jane Doe" in text
    assert "Backend Engineer" in text
    assert "https://github.com/janedoe" in text
    assert warnings == []


def test_pdf_and_docx_dispatch_use_format_extractors(tmp_path: Path, monkeypatch) -> None:
    pdf = tmp_path / "resume.pdf"
    docx = tmp_path / "resume.docx"
    pdf.write_bytes(b"%PDF-1.4")
    docx.write_bytes(b"PK")
    monkeypatch.setattr(
        "findmejobs.profile_bootstrap.extractor._extract_pdf_text",
        lambda path: ("pdf text", 1, []),
    )
    monkeypatch.setattr(
        "findmejobs.profile_bootstrap.extractor._extract_docx_text",
        lambda path: ("docx text", None, []),
    )
    assert _extract_file_text(pdf)[0] == "pdf text"
    assert _extract_file_text(docx)[0] == "docx text"


def test_import_uses_deterministic_baseline_when_openclaw_result_is_pending(
    fixtures_dir: Path,
    bootstrap_paths: tuple[Path, Path],
    monkeypatch,
) -> None:
    state_root, config_root = bootstrap_paths
    monkeypatch.setattr(
        "findmejobs.profile_bootstrap.service.FilesystemProfileBootstrapOpenClawClient",
        PendingClient,
    )
    service = ProfileBootstrapService(state_root=state_root, config_root=config_root)

    metadata = service.import_resume(file_path=fixtures_dir / "resume.txt", pasted_text=None)

    paths = prepare_paths(state_root, config_root)
    assert metadata.extraction_pending is True
    assert paths.extracted_text_path.exists()
    assert "Jane Doe" in paths.extracted_text_path.read_text(encoding="utf-8")
    profile = load_yaml(paths.profile_draft_path)
    missing = load_yaml(paths.missing_fields_path)
    raw_response = json.loads(paths.raw_draft_response_path.read_text(encoding="utf-8"))
    assert profile["full_name"] == "Jane Doe"
    assert profile["headline"] == "Senior Backend Engineer"
    assert profile["target_titles"] == ["Backend Engineer", "Platform Engineer"]
    assert profile["required_skills"] == ["Python", "SQL", "FastAPI", "AWS"]
    assert not any(item["field"] == "target_titles" for item in missing["missing"])
    assert raw_response["target_titles"] == ["Backend Engineer", "Platform Engineer"]
    meta_payload = json.loads(paths.extracted_meta_path.read_text(encoding="utf-8"))
    assert meta_payload["extraction_pending"] is True
    assert meta_payload["stored_input_path"].endswith("resume.txt")
    assert meta_payload["char_count"] > 0
    assert meta_payload["original_sha256"]
    assert meta_payload["extracted_text_sha256"]
    assert meta_payload["detected_links"] == []


def test_import_generates_profile_and_ranking_drafts_with_openclaw_result(
    fixtures_dir: Path,
    bootstrap_paths: tuple[Path, Path],
    monkeypatch,
) -> None:
    state_root, config_root = bootstrap_paths
    monkeypatch.setattr(
        "findmejobs.profile_bootstrap.service.FilesystemProfileBootstrapOpenClawClient",
        ResultClient,
    )
    service = ProfileBootstrapService(state_root=state_root, config_root=config_root)

    metadata = service.import_resume(file_path=fixtures_dir / "resume.txt", pasted_text=None)

    paths = prepare_paths(state_root, config_root)
    profile = load_yaml(paths.profile_draft_path)
    ranking = load_yaml(paths.ranking_draft_path)
    report = paths.import_report_path.read_text(encoding="utf-8")
    raw_response = json.loads(paths.raw_draft_response_path.read_text(encoding="utf-8"))
    assert metadata.extraction_pending is False
    assert profile["full_name"] == "Jane Doe"
    assert profile["headline"] == "Senior Backend Engineer"
    assert profile["github_url"] == "https://github.com/janedoe"
    assert profile["target_titles"] == ["Backend Engineer", "Platform Engineer"]
    assert ranking["minimum_salary"] is None
    assert ranking["require_remote"] is None
    assert ranking["blocked_companies"] is None
    assert raw_response["import_id"] == metadata.import_id
    assert "allowed_countries" in report
    assert "Low Confidence Fields" in report
    assert not paths.canonical_profile_path.exists()
    assert not paths.canonical_ranking_path.exists()


def test_pasted_text_import_works(
    bootstrap_paths: tuple[Path, Path],
    monkeypatch,
) -> None:
    state_root, config_root = bootstrap_paths
    monkeypatch.setattr(
        "findmejobs.profile_bootstrap.service.FilesystemProfileBootstrapOpenClawClient",
        PendingClient,
    )
    service = ProfileBootstrapService(state_root=state_root, config_root=config_root)

    metadata = service.import_resume(
        file_path=None,
        pasted_text="Jane Doe jane@example.com Backend Engineer Python SQL",
    )

    paths = prepare_paths(state_root, config_root)
    assert metadata.original_filename == "pasted.txt"
    assert paths.extracted_text_path.read_text(encoding="utf-8").startswith("Jane Doe")
    assert Path(metadata.stored_input_path).name == "pasted.txt"
    assert load_yaml(paths.profile_draft_path)["full_name"] == "Jane Doe"


def test_refresh_pending_import_consumes_openclaw_result(
    fixtures_dir: Path,
    bootstrap_paths: tuple[Path, Path],
) -> None:
    state_root, config_root = bootstrap_paths
    service = ProfileBootstrapService(state_root=state_root, config_root=config_root, id_factory=lambda: "import-1")

    metadata = service.import_resume(file_path=fixtures_dir / "resume.txt", pasted_text=None)

    paths = prepare_paths(state_root, config_root)
    assert metadata.extraction_pending is True
    packet = json.loads(paths.review_packet_path.read_text(encoding="utf-8"))
    assert packet["import_id"] == "import-1"
    paths.review_result_path.write_text(
        ResumeExtractionDraft(
            import_id="import-1",
            full_name="Jane Doe",
            target_titles=["Backend Engineer"],
            required_skills=["Python"],
            preferred_locations=["Remote"],
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )

    refreshed = service.refresh_pending_import()

    assert refreshed.extraction_pending is False
    profile = load_yaml(paths.profile_draft_path)
    assert profile["target_titles"] == ["Backend Engineer"]
    assert json.loads(paths.raw_draft_response_path.read_text(encoding="utf-8"))["import_id"] == "import-1"


def test_refresh_pending_import_rejects_result_without_import_id(
    fixtures_dir: Path,
    bootstrap_paths: tuple[Path, Path],
) -> None:
    state_root, config_root = bootstrap_paths
    service = ProfileBootstrapService(state_root=state_root, config_root=config_root, id_factory=lambda: "import-1")
    service.import_resume(file_path=fixtures_dir / "resume.txt", pasted_text=None)
    paths = prepare_paths(state_root, config_root)
    paths.review_result_path.write_text(
        ResumeExtractionDraft(
            target_titles=["Backend Engineer"],
            required_skills=["Python"],
            preferred_locations=["Remote"],
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError):
        service.refresh_pending_import()


def test_unsupported_file_fails_clearly(
    bootstrap_paths: tuple[Path, Path],
) -> None:
    state_root, config_root = bootstrap_paths
    service = ProfileBootstrapService(state_root=state_root, config_root=config_root)
    bad_file = state_root.parent / "resume.html"
    bad_file.parent.mkdir(parents=True, exist_ok=True)
    bad_file.write_text("<html></html>", encoding="utf-8")
    with pytest.raises(ValueError):
        service.import_resume(file_path=bad_file, pasted_text=None)


def test_import_with_refinement_answers_updates_draft_conservatively(
    fixtures_dir: Path,
    bootstrap_paths: tuple[Path, Path],
    monkeypatch,
) -> None:
    state_root, config_root = bootstrap_paths
    monkeypatch.setattr(
        "findmejobs.profile_bootstrap.service.FilesystemProfileBootstrapOpenClawClient",
        RefinementClient,
    )
    service = ProfileBootstrapService(state_root=state_root, config_root=config_root)

    service.import_resume(
        file_path=fixtures_dir / "resume.txt",
        pasted_text=None,
        refinement_answers="Prefer remote work. FastAPI is a nice-to-have.",
    )

    paths = prepare_paths(state_root, config_root)
    profile = load_yaml(paths.profile_draft_path)
    ranking = load_yaml(paths.ranking_draft_path)
    assert profile["required_skills"] == ["Python", "SQL"]
    assert profile["preferred_skills"] == ["AWS", "FastAPI"]
    assert profile["preferred_locations"] == ["Remote"]
    assert ranking["require_remote"] is None


def test_validation_fails_on_missing_required_fields(bootstrap_paths: tuple[Path, Path]) -> None:
    state_root, config_root = bootstrap_paths
    service = ProfileBootstrapService(state_root=state_root, config_root=config_root)
    paths = prepare_paths(state_root, config_root)
    paths.profile_draft_path.parent.mkdir(parents=True, exist_ok=True)
    paths.profile_draft_path.write_text('{"version":"bootstrap-v1","target_titles":[],"required_skills":[],"preferred_skills":[],"preferred_locations":[],"allowed_countries":[]}', encoding="utf-8")
    paths.ranking_draft_path.write_text('{"rank_model_version":"bootstrap-v1","minimum_score":45.0,"stale_days":30}', encoding="utf-8")
    paths.missing_fields_path.write_text('{"missing":[{"field":"target_titles","reason":"required","required_for_promotion":true}],"low_confidence_fields":[]}', encoding="utf-8")
    result = service.validate_draft()
    assert result.status == "failed"
    assert "draft_practically_empty" in result.errors


def test_validation_flags_invalid_email_and_invalid_salary(bootstrap_paths: tuple[Path, Path]) -> None:
    state_root, config_root = bootstrap_paths
    service = ProfileBootstrapService(state_root=state_root, config_root=config_root)
    paths = prepare_paths(state_root, config_root)
    paths.profile_draft_path.parent.mkdir(parents=True, exist_ok=True)
    paths.profile_draft_path.write_text(
        '{"version":"bootstrap-v1","email":"bad-email","target_titles":["Backend Engineer"],"required_skills":["Python"],"preferred_locations":["Remote"]}',
        encoding="utf-8",
    )
    paths.ranking_draft_path.write_text(
        '{"rank_model_version":"bootstrap-v1","minimum_score":45.0,"stale_days":30,"minimum_salary":-10}',
        encoding="utf-8",
    )
    paths.missing_fields_path.write_text('{"missing":[],"low_confidence_fields":[]}', encoding="utf-8")
    result = service.validate_draft()
    assert "invalid_email" in result.errors
    assert "invalid_minimum_salary" in result.errors
    assert "invalid_stale_days" not in result.errors


def test_promote_draft_writes_canonical_yaml(
    fixtures_dir: Path,
    bootstrap_paths: tuple[Path, Path],
    monkeypatch,
) -> None:
    state_root, config_root = bootstrap_paths
    monkeypatch.setattr(
        "findmejobs.profile_bootstrap.service.FilesystemProfileBootstrapOpenClawClient",
        ResultClient,
    )
    service = ProfileBootstrapService(state_root=state_root, config_root=config_root)
    service.import_resume(file_path=fixtures_dir / "resume.txt", pasted_text=None)

    diff = service.promote_draft()
    paths = prepare_paths(state_root, config_root)
    assert paths.canonical_profile_path.exists()
    assert paths.canonical_ranking_path.exists()
    assert "full_name" in load_yaml(paths.canonical_profile_path)
    assert diff.protected_conflicts == []
    runtime_profile = load_profile_config(paths.canonical_profile_path)
    assert runtime_profile.target_titles == ["Backend Engineer", "Platform Engineer"]
    assert runtime_profile.summary == "Senior Backend Engineer with 7 years building Python, SQL, FastAPI, and AWS systems."


def test_invalid_draft_does_not_promote_or_write_canonical_files(bootstrap_paths: tuple[Path, Path]) -> None:
    state_root, config_root = bootstrap_paths
    service = ProfileBootstrapService(state_root=state_root, config_root=config_root)
    paths = prepare_paths(state_root, config_root)
    paths.profile_draft_path.parent.mkdir(parents=True, exist_ok=True)
    paths.profile_draft_path.write_text(
        '{"version":"bootstrap-v1","target_titles":[],"required_skills":[],"preferred_skills":[],"preferred_locations":[],"allowed_countries":[]}',
        encoding="utf-8",
    )
    paths.ranking_draft_path.write_text(
        '{"rank_model_version":"bootstrap-v1","minimum_score":45.0,"stale_days":30}',
        encoding="utf-8",
    )
    paths.missing_fields_path.write_text(
        '{"missing":[{"field":"skills","reason":"required","required_for_promotion":true}],"low_confidence_fields":[]}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        service.promote_draft()
    assert not paths.canonical_profile_path.exists()
    assert not paths.canonical_ranking_path.exists()


def test_malformed_openclaw_result_fails_clearly_and_saves_raw_response(
    fixtures_dir: Path,
    bootstrap_paths: tuple[Path, Path],
    monkeypatch,
) -> None:
    state_root, config_root = bootstrap_paths
    monkeypatch.setattr(
        "findmejobs.profile_bootstrap.service.FilesystemProfileBootstrapOpenClawClient",
        MalformedClient,
    )
    service = ProfileBootstrapService(state_root=state_root, config_root=config_root)

    with pytest.raises(RuntimeError, match="draft_generation_result_invalid"):
        service.import_resume(file_path=fixtures_dir / "resume.txt", pasted_text=None)

    paths = prepare_paths(state_root, config_root)
    assert paths.raw_draft_response_path.exists()
    assert paths.profile_draft_path.exists() is False


def test_rich_resume_text_produces_substantial_baseline_draft(
    fixtures_dir: Path,
    bootstrap_paths: tuple[Path, Path],
    monkeypatch,
) -> None:
    state_root, config_root = bootstrap_paths
    monkeypatch.setattr(
        "findmejobs.profile_bootstrap.service.FilesystemProfileBootstrapOpenClawClient",
        PendingClient,
    )
    service = ProfileBootstrapService(state_root=state_root, config_root=config_root)

    metadata = service.import_resume(file_path=fixtures_dir / "rich_resume.txt", pasted_text=None)

    paths = prepare_paths(state_root, config_root)
    profile = load_yaml(paths.profile_draft_path)
    ranking = load_yaml(paths.ranking_draft_path)
    missing = load_yaml(paths.missing_fields_path)
    report = paths.import_report_path.read_text(encoding="utf-8")

    assert metadata.char_count > 2000
    assert profile["full_name"] == "Alex Rivera"
    assert profile["headline"] == "Senior Fullstack Engineer"
    assert profile["years_experience"] == 10
    assert profile["location_text"] == "Philippines (Remote)"
    assert profile["email"] == "alex.rivera@example.test"
    assert profile["phone"] == "+63 917 111 2233"
    assert profile["github_url"] == "https://github.com/alexrivera"
    assert profile["linkedin_url"] == "https://linkedin.com/in/alexrivera"
    assert "TypeScript" in profile["required_skills"]
    assert "React" in profile["required_skills"]
    assert "Node.js" in profile["required_skills"]
    assert "AWS" in profile["required_skills"] or "AWS" in profile["preferred_skills"]
    assert "Senior Fullstack Engineer" in profile["target_titles"]
    assert "Platform Engineer" in profile["target_titles"]
    assert profile["summary"]
    assert profile["strengths"]
    assert ranking["title_families"]
    assert not any(item["field"] == "target_titles" for item in missing["missing"])
    assert "validation_status: `strong`" in report


def test_reimport_does_not_overwrite_explicit_canonical_preferences(
    fixtures_dir: Path,
    bootstrap_paths: tuple[Path, Path],
    monkeypatch,
) -> None:
    state_root, config_root = bootstrap_paths
    monkeypatch.setattr(
        "findmejobs.profile_bootstrap.service.FilesystemProfileBootstrapOpenClawClient",
        ResultClient,
    )
    service = ProfileBootstrapService(state_root=state_root, config_root=config_root)
    first_metadata = service.import_resume(file_path=fixtures_dir / "resume.txt", pasted_text=None)
    service.promote_draft()

    paths = prepare_paths(state_root, config_root)
    paths.canonical_ranking_path.write_text(
        '{"rank_model_version":"bootstrap-v1","stale_days":30,"minimum_score":45.0,"minimum_salary":90000,"require_remote":true,"blocked_companies":["Reject Co"]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "findmejobs.profile_bootstrap.service.FilesystemProfileBootstrapOpenClawClient",
        HardPreferenceClient,
    )
    service.import_resume(file_path=fixtures_dir / "resume.txt", pasted_text=None, reimport=True)
    previous_snapshot = prepare_paths(state_root, config_root).history_root / first_metadata.import_id
    assert previous_snapshot.exists()
    assert (prepare_paths(state_root, config_root).diff_path).exists()
    with pytest.raises(ValueError):
        service.promote_draft()
    ranking = load_yaml(paths.canonical_ranking_path)
    assert ranking["minimum_salary"] == 90000
    assert ranking["require_remote"] is True


def test_promotion_snapshots_previous_canonical_config(
    fixtures_dir: Path,
    bootstrap_paths: tuple[Path, Path],
    monkeypatch,
) -> None:
    state_root, config_root = bootstrap_paths
    monkeypatch.setattr(
        "findmejobs.profile_bootstrap.service.FilesystemProfileBootstrapOpenClawClient",
        ResultClient,
    )
    service = ProfileBootstrapService(state_root=state_root, config_root=config_root)
    service.import_resume(file_path=fixtures_dir / "resume.txt", pasted_text=None)
    service.promote_draft()
    paths = prepare_paths(state_root, config_root)
    first_profile = paths.canonical_profile_path.read_text(encoding="utf-8")

    service.import_resume(file_path=fixtures_dir / "resume.md", pasted_text=None, reimport=True)
    service.promote_draft()

    second_import_id = service.load_import_metadata().import_id
    snapshot_root = paths.history_root.parent / "promotions" / "snapshots" / second_import_id
    assert (snapshot_root / "profile.yaml").exists()
    assert (snapshot_root / "profile.yaml").read_text(encoding="utf-8") == first_profile
