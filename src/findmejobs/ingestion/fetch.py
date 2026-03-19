from __future__ import annotations

from pathlib import Path

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from findmejobs.config.models import AppConfig
from findmejobs.domain.source import FetchArtifact
from findmejobs.utils.hashing import sha256_hexdigest
from findmejobs.utils.time import utcnow


class FetchError(RuntimeError):
    pass


def _extension_for_content_type(content_type: str | None) -> str:
    if not content_type:
        return ".bin"
    content_type = content_type.casefold()
    if "json" in content_type:
        return ".json"
    if "xml" in content_type or "rss" in content_type:
        return ".xml"
    if "html" in content_type:
        return ".html"
    return ".bin"


def build_retryable_get(max_attempts: int):
    @retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.TransportError, FetchError)),
        reraise=True,
    )
    def _runner(client: httpx.Client, url: str) -> httpx.Response:
        response = client.get(url, follow_redirects=True)
        if response.status_code >= 500:
            raise FetchError(f"server returned {response.status_code}")
        response.raise_for_status()
        return response

    return _runner


def fetch_to_artifact(client: httpx.Client, url: str, app_config: AppConfig, raw_root: Path, source_name: str) -> FetchArtifact:
    response = build_retryable_get(app_config.http.max_attempts)(client, url)
    body = response.content
    fetched_at = utcnow()
    sha = sha256_hexdigest(body)
    extension = _extension_for_content_type(response.headers.get("content-type"))
    target_dir = raw_root / source_name / fetched_at.strftime("%Y/%m/%d")
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"{fetched_at.strftime('%H%M%S')}-{sha[:16]}{extension}"
    if not target_path.exists():
        target_path.write_bytes(body)
    return FetchArtifact(
        fetched_url=url,
        final_url=str(response.url),
        status_code=response.status_code,
        content_type=response.headers.get("content-type"),
        headers=dict(response.headers),
        fetched_at=fetched_at,
        body_bytes=body,
        sha256=sha,
        storage_path=str(target_path),
    )
