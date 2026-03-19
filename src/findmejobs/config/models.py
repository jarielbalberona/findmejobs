from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field, HttpUrl


class StorageConfig(BaseModel):
    root_dir: Path
    raw_dir: Path
    review_outbox_dir: Path
    review_inbox_dir: Path
    lock_dir: Path


class DatabaseConfig(BaseModel):
    url: str


class HttpConfig(BaseModel):
    timeout_seconds: float = 20.0
    max_attempts: int = 3
    user_agent: str = "findmejobs/0.1.0"


class LoggingConfig(BaseModel):
    level: str = "INFO"


class AppConfig(BaseModel):
    database: DatabaseConfig
    storage: StorageConfig
    http: HttpConfig = Field(default_factory=HttpConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


class SourceBaseConfig(BaseModel):
    name: str
    kind: str
    enabled: bool = True


class RSSSourceConfig(SourceBaseConfig):
    kind: Literal["rss"]
    feed_url: HttpUrl


class GreenhouseSourceConfig(SourceBaseConfig):
    kind: Literal["greenhouse"]
    board_token: str
    include_content: bool = True


SourceConfig = Annotated[RSSSourceConfig | GreenhouseSourceConfig, Field(discriminator="kind")]


class RankingWeights(BaseModel):
    title_alignment: float = 30.0
    must_have_skills: float = 35.0
    preferred_skills: float = 10.0
    location_fit: float = 10.0
    remote_fit: float = 10.0
    recency: float = 5.0


class RankingPolicy(BaseModel):
    stale_days: int = 30
    minimum_score: float = 45.0
    minimum_salary: int | None = None
    blocked_companies: list[str] = Field(default_factory=list)
    blocked_title_keywords: list[str] = Field(default_factory=list)
    require_remote: bool = False
    allowed_countries: list[str] = Field(default_factory=list)
    weights: RankingWeights = Field(default_factory=RankingWeights)


class ProfileConfig(BaseModel):
    version: str
    rank_model_version: str = "slice1-default"
    target_titles: list[str]
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    preferred_locations: list[str] = Field(default_factory=list)
    allowed_countries: list[str] = Field(default_factory=list)
    ranking: RankingPolicy = Field(default_factory=RankingPolicy)
