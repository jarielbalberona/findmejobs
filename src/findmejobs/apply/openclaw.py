from __future__ import annotations

import json
from pathlib import Path

from findmejobs.apply.models import ApplyBrowserRequest, ApplyBrowserResult


class FilesystemApplyOpenClawClient:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def export_browser_request(self, request: ApplyBrowserRequest) -> Path:
        target = self.root_dir / "browser.request.json"
        target.write_text(request.model_dump_json(indent=2), encoding="utf-8")
        return target

    def load_browser_result(self) -> ApplyBrowserResult | None:
        target = self.root_dir / "browser.result.json"
        if not target.exists():
            return None
        return ApplyBrowserResult.model_validate(json.loads(target.read_text(encoding="utf-8")))
