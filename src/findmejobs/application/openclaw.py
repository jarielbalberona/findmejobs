from __future__ import annotations

import json
from pathlib import Path

from findmejobs.application.models import (
    AnswerDraftRequestModel,
    AnswerDraftResultModel,
    CoverLetterDraftRequestModel,
    CoverLetterDraftResultModel,
)


class FilesystemApplicationDraftOpenClawClient:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def export_cover_letter_request(self, request: CoverLetterDraftRequestModel) -> Path:
        target = self.root_dir / "cover_letter.request.json"
        target.write_text(request.model_dump_json(indent=2), encoding="utf-8")
        return target

    def export_answers_request(self, request: AnswerDraftRequestModel) -> Path:
        target = self.root_dir / "answers.request.json"
        target.write_text(request.model_dump_json(indent=2), encoding="utf-8")
        return target

    def load_cover_letter_result(self) -> CoverLetterDraftResultModel | None:
        target = self.root_dir / "cover_letter.result.json"
        if not target.exists():
            return None
        return CoverLetterDraftResultModel.model_validate(json.loads(target.read_text(encoding="utf-8")))

    def load_answers_result(self) -> AnswerDraftResultModel | None:
        target = self.root_dir / "answers.result.json"
        if not target.exists():
            return None
        return AnswerDraftResultModel.model_validate(json.loads(target.read_text(encoding="utf-8")))
