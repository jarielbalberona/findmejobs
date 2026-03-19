from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


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


class EmailDeliveryConfig(BaseModel):
    enabled: bool = False
    host: str | None = None
    port: int = 587
    username: str | None = None
    password: str | None = None
    use_tls: bool = True
    sender: str | None = None
    recipient: str | None = None

    @model_validator(mode="after")
    def reject_inline_password(self) -> "EmailDeliveryConfig":
        # Keep SMTP passwords out of config files/shell history; use env var instead.
        if self.password not in (None, ""):
            raise ValueError("delivery.email.password is not supported; use FINDMEJOBS_SMTP_PASSWORD")
        return self


class DeliveryConfig(BaseModel):
    channel: Literal["email"] = "email"
    daily_hour: int = 8
    digest_max_items: int = 10
    email: EmailDeliveryConfig = Field(default_factory=EmailDeliveryConfig)


class AppConfig(BaseModel):
    database: DatabaseConfig
    storage: StorageConfig
    http: HttpConfig = Field(default_factory=HttpConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    delivery: DeliveryConfig = Field(default_factory=DeliveryConfig)


class SourceBaseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    kind: str
    enabled: bool = True
    priority: int = Field(default=0, ge=0)
    trust_weight: float = Field(default=1.0, gt=0.0)
    fetch_cap: int | None = Field(default=None, gt=0)
    blocked_title_keywords: list[str] = Field(default_factory=list)


class RSSSourceConfig(SourceBaseConfig):
    kind: Literal["rss"]
    feed_url: HttpUrl


class GreenhouseSourceConfig(SourceBaseConfig):
    kind: Literal["greenhouse"]
    board_token: str
    include_content: bool = True
    company_name: str | None = None


class LeverSourceConfig(SourceBaseConfig):
    kind: Literal["lever"]
    site: str
    company_name: str | None = None


class SmartRecruitersSourceConfig(SourceBaseConfig):
    kind: Literal["smartrecruiters"]
    company_identifier: str
    limit: int = 100
    company_name: str | None = None


class WorkableSourceConfig(SourceBaseConfig):
    kind: Literal["workable"]
    account_subdomain: str
    include_details: bool = True
    company_name: str | None = None


class AshbySourceConfig(SourceBaseConfig):
    kind: Literal["ashby"]
    board_url: HttpUrl
    company_name: str | None = None


class JobStreetPHSourceConfig(SourceBaseConfig):
    kind: Literal["jobstreet_ph"]
    board_url: HttpUrl
    company_name: str | None = None
    trust_weight: float = Field(default=0.7, gt=0.0)


class KalibrrSourceConfig(SourceBaseConfig):
    kind: Literal["kalibrr"]
    board_url: HttpUrl
    company_name: str | None = None
    trust_weight: float = Field(default=0.75, gt=0.0)


class BossjobPHSourceConfig(SourceBaseConfig):
    kind: Literal["bossjob_ph"]
    board_url: HttpUrl
    company_name: str | None = None
    trust_weight: float = Field(default=0.65, gt=0.0)


class FounditPHSourceConfig(SourceBaseConfig):
    kind: Literal["foundit_ph"]
    board_url: HttpUrl
    company_name: str | None = None
    trust_weight: float = Field(default=0.7, gt=0.0)


class DirectPageSourceConfig(SourceBaseConfig):
    kind: Literal["direct_page"]
    page_url: HttpUrl
    company_name: str | None = None


SourceConfig = Annotated[
    RSSSourceConfig
    | GreenhouseSourceConfig
    | LeverSourceConfig
    | SmartRecruitersSourceConfig
    | WorkableSourceConfig
    | AshbySourceConfig
    | JobStreetPHSourceConfig
    | KalibrrSourceConfig
    | BossjobPHSourceConfig
    | FounditPHSourceConfig
    | DirectPageSourceConfig,
    Field(discriminator="kind"),
]


class SourcesFileConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = "v1"
    sources: list[SourceConfig] = Field(default_factory=list)


class RankingWeights(BaseModel):
    title_alignment: float = 30.0
    title_family: float = 10.0
    must_have_skills: float = 35.0
    preferred_skills: float = 10.0
    location_fit: float = 10.0
    remote_fit: float = 10.0
    recency: float = 5.0
    company_preference: float = 5.0
    timezone_fit: float = 5.0
    source_trust: float = 5.0
    feedback_signal: float = 5.0


class RankingPolicy(BaseModel):
    stale_days: int = 30
    minimum_score: float = 45.0
    minimum_salary: int | None = None
    blocked_companies: list[str] = Field(default_factory=list)
    blocked_title_keywords: list[str] = Field(default_factory=list)
    require_remote: bool = False
    remote_first: bool = False
    allowed_countries: list[str] = Field(default_factory=list)
    allowed_companies: list[str] = Field(default_factory=list)
    preferred_companies: list[str] = Field(default_factory=list)
    preferred_timezones: list[str] = Field(default_factory=list)
    title_families: dict[str, list[str]] = Field(default_factory=dict)
    weights: RankingWeights = Field(default_factory=RankingWeights)


class ApplicationProfile(BaseModel):
    professional_summary: str | None = None
    key_achievements: list[str] = Field(default_factory=list)
    project_highlights: list[str] = Field(default_factory=list)
    salary_expectation: str | None = None
    notice_period: str | None = None
    current_availability: str | None = None
    remote_preference: str | None = None
    relocation_preference: str | None = None
    work_authorization: str | None = None
    work_hours: str | None = None


class ProfileConfig(BaseModel):
    version: str
    rank_model_version: str = "slice2-default"
    full_name: str | None = None
    headline: str | None = None
    email: str | None = None
    phone: str | None = None
    location_text: str | None = None
    github_url: str | None = None
    linkedin_url: str | None = None
    years_experience: int | None = None
    summary: str | None = None
    strengths: list[str] = Field(default_factory=list)
    recent_titles: list[str] = Field(default_factory=list)
    recent_companies: list[str] = Field(default_factory=list)
    target_titles: list[str]
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    preferred_locations: list[str] = Field(default_factory=list)
    allowed_countries: list[str] = Field(default_factory=list)
    ranking: RankingPolicy = Field(default_factory=RankingPolicy)
    application: ApplicationProfile = Field(default_factory=ApplicationProfile)
