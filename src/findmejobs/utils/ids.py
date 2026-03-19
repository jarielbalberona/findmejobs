from __future__ import annotations

from uuid import uuid4


def new_id() -> str:
    return uuid4().hex[:26]
