from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PREFIXES = ("utm_", "fbclid", "gclid")
KEEP_QUERY_KEYS = frozenset({"gh_jid", "lever-source", "jobId", "job_id"})


def canonicalize_url(value: str | None) -> str | None:
    if not value:
        return None
    parts = urlsplit(value.strip())
    if not parts.scheme or not parts.netloc:
        return None
    query_pairs = []
    for key, query_value in parse_qsl(parts.query, keep_blank_values=False):
        lowered = key.casefold()
        if lowered in KEEP_QUERY_KEYS:
            query_pairs.append((key, query_value))
            continue
        if lowered.startswith(TRACKING_PREFIXES):
            continue
    normalized_path = parts.path.rstrip("/") or "/"
    query = urlencode(sorted(query_pairs))
    return urlunsplit(
        (
            parts.scheme.casefold(),
            parts.netloc.casefold(),
            normalized_path,
            query,
            "",
        )
    )
