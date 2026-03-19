from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path

from findmejobs.profile_bootstrap.models import ProfileExtractionPacket, ProfileRefinementPacket, ResumeExtractionDraft


class OpenClawExtractionPending(RuntimeError):
    pass


class ProfileBootstrapOpenClawClient(ABC):
    @abstractmethod
    def export_request(self, packet: ProfileExtractionPacket | ProfileRefinementPacket) -> Path:
        raise NotImplementedError

    @abstractmethod
    def load_result(self, expected_import_id: str) -> ResumeExtractionDraft | None:
        raise NotImplementedError


class FilesystemProfileBootstrapOpenClawClient(ProfileBootstrapOpenClawClient):
    def __init__(self, request_path: Path, result_path: Path) -> None:
        self.request_path = request_path
        self.result_path = result_path
        self.request_path.parent.mkdir(parents=True, exist_ok=True)
        self.result_path.parent.mkdir(parents=True, exist_ok=True)

    def export_request(self, packet: ProfileExtractionPacket | ProfileRefinementPacket) -> Path:
        if self.result_path.exists():
            self.result_path.unlink()
        self.request_path.write_text(packet.model_dump_json(indent=2), encoding="utf-8")
        return self.request_path

    def load_result(self, expected_import_id: str) -> ResumeExtractionDraft | None:
        if not self.result_path.exists():
            return None
        payload = json.loads(self.result_path.read_text(encoding="utf-8"))
        result = ResumeExtractionDraft.model_validate(payload)
        if result.import_id != expected_import_id:
            raise ValueError(f"stale_openclaw_result:{result.import_id}")
        return result
