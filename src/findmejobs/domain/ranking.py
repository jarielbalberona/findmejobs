from __future__ import annotations

from pydantic import BaseModel, Field


class ScoreBreakdown(BaseModel):
    hard_filter_reasons: list[str] = Field(default_factory=list)
    components: dict[str, float] = Field(default_factory=dict)

    @property
    def total(self) -> float:
        return round(sum(self.components.values()), 2)
