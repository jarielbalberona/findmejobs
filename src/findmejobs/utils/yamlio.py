from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def dump_yaml(data: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import yaml  # type: ignore[import-not-found]

        payload = yaml.safe_dump(data, sort_keys=False, allow_unicode=False)
    except ImportError:
        payload = json.dumps(data, indent=2, ensure_ascii=True)
    path.write_text(payload, encoding="utf-8")


def load_yaml(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore[import-not-found]

        return yaml.safe_load(text)
    except ImportError:
        return json.loads(text)
