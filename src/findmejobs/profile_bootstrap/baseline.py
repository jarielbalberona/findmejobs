from __future__ import annotations

import re

from findmejobs.profile_bootstrap.models import ResumeExtractionDraft
from findmejobs.utils.text import collapse_whitespace

EMAIL_RE = re.compile(r"(?P<email>[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})", re.IGNORECASE)
PHONE_RE = re.compile(r"(?P<phone>\+?\d(?:[\s().-]*\d){8,}\d)")
YEARS_RE = re.compile(r"(?P<years>\d{1,2})\+?\s+years?(?:\s+of\s+experience)?", re.IGNORECASE)
NAME_WITH_SUMMARY_RE = re.compile(r"^(?P<name>[A-Z][A-Z .'-]{3,80}?)\s+SUMMARY\b")
NAME_TITLE_RE = re.compile(r"^(?P<name>[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\b")
SUMMARY_SECTION_RE = re.compile(
    r"(?:SUMMARY|PROFILE|PROFESSIONAL SUMMARY)\s*[:\-]?\s*(?P<section>.+?)(?=(?:CORE SKILLS|SKILLS|EXPERIENCE|WORK EXPERIENCE|PRODUCT ENGINEERING|EARLIER EXPERIENCE|$))",
    re.IGNORECASE,
)
SKILLS_SECTION_RE = re.compile(
    r"(?:CORE SKILLS|SKILLS)\s*[:\-]?\s*(?P<section>.+?)(?=(?:EXPERIENCE|WORK EXPERIENCE|PRODUCT ENGINEERING|EARLIER EXPERIENCE|EDUCATION|$))",
    re.IGNORECASE,
)
TARGET_TITLES_RE = re.compile(r"(?:Target roles?|Target titles?|Seeking)\s*:\s*(?P<titles>[^.]+)", re.IGNORECASE)
PREFERRED_LOCATIONS_RE = re.compile(
    r"(?:Prefers?|Preferred)\s+(?P<preference>remote(?:\s+work)?(?:\s+across\s+[^.]+)?)",
    re.IGNORECASE,
)
RECENT_ROLE_RE = re.compile(
    r"(?P<company>[A-Z][A-Za-z0-9&.,'()/+\- ]{1,80}?)\s+[—-]\s+(?P<title>[A-Z][A-Za-z0-9 /&.+\-]{2,80}?(?:Engineer|Developer|Architect|Lead|Manager|Consultant|Specialist|Administrator))(?=\s*(?:\(|[A-Z][a-z]{2}\s+\d{4}|Tech:|•|Page|\||$))"
)
LOCATION_RE = re.compile(
    r"(?P<location>(?:[A-Z][A-Za-z]+,\s*[A-Z][A-Za-z]+|Philippines(?:\s*\(Remote\))?|Singapore|Australia|Canada|United States|Remote(?:\s*\([^)]+\))?))"
)
GITHUB_RE = re.compile(r"(?P<url>(?:https?://)?github\.com/[A-Za-z0-9_.-]+)", re.IGNORECASE)
LINKEDIN_RE = re.compile(r"(?P<url>(?:https?://)?linkedin\.com/[A-Za-z0-9_./-]+)", re.IGNORECASE)
TITLE_KEYWORDS_RE = re.compile(
    r"(?P<title>(?:Senior|Lead|Principal|Staff)?\s*(?:Fullstack|Full Stack|Frontend|Front-end|Backend|Back-end|Platform|Software|DevOps)\s+(?:Engineer|Developer|Architect))",
    re.IGNORECASE,
)
TITLE_SUMMARY_SENTENCE_RE = re.compile(
    r"(?P<sentence>(?:Senior|Lead|Principal|Staff)?\s*(?:Fullstack|Full Stack|Frontend|Front-end|Backend|Back-end|Platform|Software|DevOps)\s+(?:Engineer|Developer|Architect)[^.]*?\bwith\b[^.]*?\d{1,2}\s+years?(?:\s+of\s+experience)?[^.]*\.)",
    re.IGNORECASE,
)

COUNTRY_CODES = {
    "philippines": "PH",
    "singapore": "SG",
    "australia": "AU",
    "canada": "CA",
    "united states": "US",
}

SKILL_PRIORITIES = [
    "TypeScript",
    "Python",
    "SQL",
    "React",
    "Node.js",
    "PostgreSQL",
    "FastAPI",
    "AWS",
    "Terraform",
    "Docker",
    "Next.js",
    "Vite",
    "Tailwind CSS",
    "NestJS",
    "REST APIs",
    "Prisma",
    "Drizzle ORM",
    "Redis",
    "WebSockets",
    "GitHub Actions",
    "Azure",
    "Grafana",
    "Prometheus",
    "Loki",
]


def build_baseline_extraction(import_id: str, resume_text: str) -> ResumeExtractionDraft:
    text = collapse_whitespace(resume_text)
    full_name = _extract_name(text)
    email = _extract_match(text, EMAIL_RE, "email")
    phone = _extract_phone(text)
    github_url = _normalize_url(_extract_match(text, GITHUB_RE, "url"))
    linkedin_url = _normalize_url(_extract_match(text, LINKEDIN_RE, "url"))
    years_experience = _extract_years_experience(text)
    location_text = _extract_location(text, email=email)
    headline = _extract_headline(text, full_name=full_name, email=email)
    summary = _extract_summary(text)
    skills = _extract_skills(text)
    recent_companies, recent_titles = _extract_recent_roles(text)
    target_titles, target_titles_low_confidence = _extract_target_titles(text, headline=headline, recent_titles=recent_titles)
    preferred_locations, allowed_countries = _extract_location_preferences(text)
    strengths = _extract_strengths(text, headline=headline, summary=summary, skills=skills)
    required_skills, preferred_skills = _split_skills(skills)
    title_families = _build_title_families(target_titles, recent_titles)

    evidence = {
        "full_name": _snippets(full_name),
        "headline": _snippets(headline),
        "email": _snippets(email),
        "phone": _snippets(phone),
        "location_text": _snippets(location_text),
        "summary": _snippets(summary),
        "target_titles": target_titles[:3],
        "required_skills": required_skills[:6],
        "recent_titles": recent_titles[:4],
        "recent_companies": recent_companies[:4],
    }
    evidence = {field: values for field, values in evidence.items() if values}

    low_confidence_fields: list[str] = []
    if target_titles_low_confidence:
        low_confidence_fields.append("target_titles")
    if summary is None and headline is not None:
        low_confidence_fields.append("summary")

    return ResumeExtractionDraft(
        import_id=import_id,
        full_name=full_name,
        headline=headline,
        email=email,
        phone=phone,
        location_text=location_text,
        github_url=github_url,
        linkedin_url=linkedin_url,
        years_experience=years_experience,
        summary=summary,
        strengths=strengths,
        recent_titles=recent_titles,
        recent_companies=recent_companies,
        target_titles=target_titles,
        required_skills=required_skills,
        preferred_skills=preferred_skills,
        preferred_locations=preferred_locations,
        allowed_countries=allowed_countries,
        preferred_timezones=[],
        title_families=title_families,
        evidence=evidence,
        low_confidence_fields=low_confidence_fields,
        explicit_fields=[],
    )


def _extract_name(text: str) -> str | None:
    if match := NAME_WITH_SUMMARY_RE.search(text):
        return _normalize_person_name(match.group("name"))
    if match := NAME_TITLE_RE.search(text):
        return _normalize_person_name(match.group("name"))
    if email_match := EMAIL_RE.search(text):
        prefix = collapse_whitespace(text[: email_match.start()])
        for candidate in (prefix, " ".join(prefix.split()[:4])):
            if match := NAME_TITLE_RE.search(candidate):
                return _normalize_person_name(match.group("name"))
    return None


def _extract_headline(text: str, *, full_name: str | None, email: str | None) -> str | None:
    if match := SUMMARY_SECTION_RE.search(text):
        section = collapse_whitespace(match.group("section"))
        if headline := _headline_from_text(section):
            return headline
    header = text
    if email and email in header:
        header = header[: header.index(email)]
    if full_name and header.startswith(full_name):
        header = header[len(full_name) :].strip()
    header = header.replace("SUMMARY", " ").strip(" |")
    if headline := _headline_from_text(header):
        return headline
    if match := TITLE_KEYWORDS_RE.search(text):
        return collapse_whitespace(match.group("title"))
    return None


def _headline_from_text(text: str) -> str | None:
    if not text:
        return None
    if match := TITLE_KEYWORDS_RE.search(text):
        return collapse_whitespace(match.group("title"))
    if " with " in text.casefold():
        return collapse_whitespace(text.split(" with ", 1)[0].strip(" |"))
    parts = [collapse_whitespace(part) for part in text.split("|")]
    for part in parts:
        if TITLE_KEYWORDS_RE.search(part):
            return part
    return parts[0] if parts and len(parts[0].split()) <= 6 else None


def _extract_summary(text: str) -> str | None:
    if section := _extract_section(
        text,
        start_markers=["SUMMARY", "PROFILE", "PROFESSIONAL SUMMARY"],
        end_markers=["CORE SKILLS", "SKILLS", "WORK EXPERIENCE", "PRODUCT ENGINEERING", "EARLIER EXPERIENCE"],
    ):
        if sentence_match := TITLE_SUMMARY_SENTENCE_RE.search(section):
            return collapse_whitespace(sentence_match.group("sentence"))
        if title_match := TITLE_KEYWORDS_RE.search(section):
            end = section.find(".", title_match.start())
            if end != -1:
                return collapse_whitespace(section[title_match.start() : end + 1])
            section = section[title_match.start() :]
        return _sentences(section, limit=2)
    if sentence_match := TITLE_SUMMARY_SENTENCE_RE.search(text):
        return collapse_whitespace(sentence_match.group("sentence"))
    if title_match := TITLE_KEYWORDS_RE.search(text):
        end = text.find(".", title_match.start())
        if end != -1:
            return collapse_whitespace(text[title_match.start() : end + 1])
    first_period = text.find(".")
    if first_period == -1:
        return None
    return collapse_whitespace(text[: first_period + 1])


def _extract_skills(text: str) -> list[str]:
    raw_skills: list[str] = []
    if section := _extract_section(
        text,
        start_markers=["CORE SKILLS", "SKILLS"],
        end_markers=["EXPERIENCE", "WORK EXPERIENCE", "PRODUCT ENGINEERING", "EARLIER EXPERIENCE", "EDUCATION"],
    ):
        raw_skills.extend(_split_items(section))
    if match := re.search(r"(?:Tech|Skills)\s*:\s*(?P<section>[^•]+)", text, re.IGNORECASE):
        raw_skills.extend(_split_items(match.group("section")))
    if match := re.search(r"building\s+(?P<section>[^.]+)", text, re.IGNORECASE):
        raw_skills.extend(_split_items(match.group("section")))

    normalized: list[str] = []
    seen: set[str] = set()
    for preferred in SKILL_PRIORITIES:
        for skill in raw_skills:
            if preferred.casefold() != skill.casefold():
                continue
            key = preferred.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(preferred)
    for skill in raw_skills:
        key = skill.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(skill)
    return normalized[:16]


def _split_skills(skills: list[str]) -> tuple[list[str], list[str]]:
    if len(skills) <= 6:
        return skills, []
    return skills[:6], skills[6:12]


def _extract_target_titles(text: str, *, headline: str | None, recent_titles: list[str]) -> tuple[list[str], bool]:
    if match := TARGET_TITLES_RE.search(text):
        return _split_items(match.group("titles"))[:6], False

    candidates: list[str] = []
    if headline:
        candidates.append(headline)
        family = _title_family_candidates(headline)
        candidates.extend(family)
    for title in recent_titles[:3]:
        candidates.extend(_title_family_candidates(title))
        candidates.append(title)
    return _dedupe(candidates)[:6], True


def _extract_location(text: str, *, email: str | None) -> str | None:
    if "Location:" in text:
        if match := re.search(r"Location:\s*(?P<location>[^.]+)", text, re.IGNORECASE):
            return collapse_whitespace(match.group("location"))
    if email and email in text:
        prefix = collapse_whitespace(text[: text.index(email)])
        locations = LOCATION_RE.findall(prefix)
        if locations:
            return collapse_whitespace(locations[0])
    locations = LOCATION_RE.findall(text[:300])
    if locations:
        return collapse_whitespace(locations[0])
    return None


def _extract_location_preferences(text: str) -> tuple[list[str], list[str]]:
    preferred_locations: list[str] = []
    allowed_countries: list[str] = []
    if match := PREFERRED_LOCATIONS_RE.search(text):
        preference = collapse_whitespace(match.group("preference"))
        preferred_locations.extend(_split_location_phrase(preference))
        allowed_countries.extend(_country_codes_from_text(preference))
    return _dedupe(preferred_locations), _dedupe(allowed_countries)


def _extract_recent_roles(text: str) -> tuple[list[str], list[str]]:
    companies: list[str] = []
    titles: list[str] = []
    for match in RECENT_ROLE_RE.finditer(text):
        company = collapse_whitespace(match.group("company"))
        if ")" in company:
            company = collapse_whitespace(company.rsplit(")", 1)[-1])
        title = collapse_whitespace(match.group("title"))
        if any(token in company.casefold() for token in ("experience", "prompt", "page 1/2", "page 2/2", "remote (")):
            continue
        if not company:
            continue
        companies.append(company)
        titles.append(title)
        if len(companies) >= 5:
            break
    return _dedupe(companies), _dedupe(titles)


def _extract_strengths(text: str, *, headline: str | None, summary: str | None, skills: list[str]) -> list[str]:
    strengths: list[str] = []
    if headline and "|" in text[:200]:
        header_tail = text[:200].split(headline, 1)[-1]
        strengths.extend(_split_items(header_tail))
    if summary:
        for phrase in (
            "system architecture",
            "cloud + devops",
            "legacy modernization",
            "scalable web systems",
            "production-ready platforms",
            "react and typescript",
            "node.js apis",
        ):
            if phrase in summary.casefold():
                strengths.append(_display_phrase(phrase))
    strengths.extend(skills[:4])
    return _dedupe(strengths)[:6]


def _build_title_families(target_titles: list[str], recent_titles: list[str]) -> dict[str, list[str]]:
    families: dict[str, list[str]] = {}
    for title in [*target_titles, *recent_titles]:
        lowered = title.casefold()
        if "fullstack" in lowered or "full stack" in lowered:
            families.setdefault("fullstack", []).append(title)
        if "frontend" in lowered or "front-end" in lowered:
            families.setdefault("frontend", []).append(title)
        if "backend" in lowered or "back-end" in lowered:
            families.setdefault("backend", []).append(title)
        if "platform" in lowered:
            families.setdefault("platform", []).append(title)
        if "devops" in lowered:
            families.setdefault("devops", []).append(title)
        if "software engineer" in lowered:
            families.setdefault("software_engineering", []).append(title)
    return {family: _dedupe(values) for family, values in families.items() if values}


def _extract_years_experience(text: str) -> int | None:
    if match := YEARS_RE.search(text):
        return int(match.group("years"))
    return None


def _extract_match(text: str, pattern: re.Pattern[str], group: str) -> str | None:
    if match := pattern.search(text):
        return collapse_whitespace(match.group(group))
    return None


def _extract_phone(text: str) -> str | None:
    if match := PHONE_RE.search(text):
        return collapse_whitespace(match.group("phone"))
    return None


def _normalize_url(value: str | None) -> str | None:
    if value is None:
        return None
    if value.startswith("http://") or value.startswith("https://"):
        return value
    return f"https://{value}"


def _split_items(value: str) -> list[str]:
    cleaned_value = collapse_whitespace(re.sub(r"\([^)]*\)", "", value))
    raw = re.split(r"[•·|;,]|(?:\s+-\s+)|(?:\band\b)", cleaned_value)
    items: list[str] = []
    for item in raw:
        cleaned = collapse_whitespace(re.sub(r"\([^)]*\)", "", item)).strip(".,:()")
        cleaned = re.sub(r"\b(systems?|patterns?|exposure|foundation)\b$", "", cleaned, flags=re.IGNORECASE).strip(" .,")
        if not cleaned or len(cleaned) > 80:
            continue
        if cleaned.casefold() in {"summary", "experience", "skills", "tech"}:
            continue
        items.append(cleaned)
    return _dedupe(items)


def _split_location_phrase(value: str) -> list[str]:
    locations = []
    if "remote" in value.casefold():
        locations.append("Remote")
    for country in COUNTRY_CODES:
        if country in value.casefold():
            locations.append(country.title())
    return _dedupe(locations)


def _country_codes_from_text(value: str) -> list[str]:
    matches: list[str] = []
    lowered = value.casefold()
    for country, code in COUNTRY_CODES.items():
        if country in lowered:
            matches.append(code)
    return matches


def _title_family_candidates(title: str) -> list[str]:
    lowered = title.casefold()
    candidates = [title]
    if "fullstack" in lowered or "full stack" in lowered:
        candidates.extend(["Fullstack Engineer", "Senior Fullstack Engineer", "Software Engineer", "Platform Engineer"])
    if "backend" in lowered or "back-end" in lowered:
        candidates.extend(["Backend Engineer", "Platform Engineer", "Software Engineer"])
    if "frontend" in lowered or "front-end" in lowered:
        candidates.extend(["Frontend Engineer", "Software Engineer"])
    if "platform" in lowered or "devops" in lowered:
        candidates.extend(["Platform Engineer", "DevOps Engineer", "Software Engineer"])
    if "architect" in lowered:
        candidates.extend(["Software Architect", "Solutions Architect"])
    return _dedupe(candidates)


def _display_phrase(value: str) -> str:
    mapping = {
        "system architecture": "System Architecture",
        "cloud + devops": "Cloud + DevOps",
        "legacy modernization": "Legacy Modernization",
        "scalable web systems": "Scalable Web Systems",
        "production-ready platforms": "Production-ready Platforms",
        "react and typescript": "React and TypeScript",
        "node.js apis": "Node.js APIs",
    }
    return mapping[value]


def _normalize_person_name(value: str) -> str:
    parts = [part for part in re.split(r"\s+", collapse_whitespace(value)) if part]
    normalized = []
    for part in parts[:4]:
        if part.isupper():
            normalized.append(part.capitalize())
        else:
            normalized.append(part)
    return " ".join(normalized)


def _sentences(value: str, *, limit: int) -> str | None:
    parts = re.split(r"(?<=[.!?])\s+", collapse_whitespace(value))
    selected = [part for part in parts if part][:limit]
    if not selected:
        return None
    return collapse_whitespace(" ".join(selected))


def _snippets(value: str | None) -> list[str]:
    if value is None:
        return []
    return [value]


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = collapse_whitespace(value)
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(cleaned)
    return deduped


def _extract_section(text: str, *, start_markers: list[str], end_markers: list[str]) -> str | None:
    upper = text.upper()
    start_index = -1
    start_marker_length = 0
    for marker in start_markers:
        index = upper.find(marker.upper())
        if index == -1:
            continue
        start_index = index
        start_marker_length = len(marker)
        break
    if start_index == -1:
        return None
    section_start = start_index + start_marker_length
    section_end = len(text)
    for marker in end_markers:
        index = upper.find(marker.upper(), section_start)
        if index == -1:
            continue
        section_end = min(section_end, index)
    section = collapse_whitespace(text[section_start:section_end])
    return section or None
