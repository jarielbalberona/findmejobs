# Profile audit report

## Runtime profile source of truth

- Loader: `findmejobs.config.loader.load_profile_config` (see `src/findmejobs/config/loader.py`).
- Canonical paths: `/Volumes/Files/softwareengineering/my-projects/findmejobs/config/profile.yaml` plus sibling `config/ranking.yaml`.

## Effective ProfileConfig (sanitized JSON)

```json
{
  "version": "bootstrap-v1",
  "rank_model_version": "bootstrap-v1",
  "full_name": "Jariel Balberona",
  "headline": "Senior Fullstack Engineer",
  "email": "jarielbalb@gmail.com",
  "phone": "+63 917 657 0260",
  "location_text": "Philippines (Remote)",
  "github_url": "https://github.com/jarielbalberona",
  "linkedin_url": "https://linkedin.com/in/jarielbalberona",
  "years_experience": 9,
  "summary": "Senior Fullstack Engineer with 9 years of experience and strong depth in system architecture and DevOps.",
  "strengths": [
    "System Architecture",
    "Cloud + DevOps",
    "TypeScript",
    "React",
    "Node.js",
    "PostgreSQL"
  ],
  "recent_titles": [
    "Senior Software Engineer",
    "Software Engineer",
    "Web Software Developer",
    "Fullstack JavaScript Developer"
  ],
  "recent_companies": [
    "PRIVV",
    "DataGPT AI",
    "Experience Digital",
    "Arcanys",
    "IdeaRobin"
  ],
  "target_titles": [
    "Senior Fullstack Engineer",
    "Fullstack Engineer",
    "Software Engineer",
    "Platform Engineer",
    "Web Software Developer",
    "Fullstack JavaScript Developer"
  ],
  "required_skills": [
    "TypeScript",
    "React",
    "Node.js",
    "PostgreSQL",
    "AWS",
    "Terraform"
  ],
  "preferred_skills": [
    "Docker",
    "Next.js",
    "Vite",
    "NestJS",
    "REST APIs",
    "Redis"
  ],
  "preferred_locations": [],
  "allowed_countries": [],
  "ranking": {
    "stale_days": 30,
    "minimum_score": 45.0,
    "minimum_salary": null,
    "blocked_companies": [],
    "blocked_title_keywords": [],
    "require_remote": false,
    "remote_first": false,
    "allowed_countries": [],
    "allowed_companies": [],
    "preferred_companies": [],
    "preferred_timezones": [],
    "title_families": {
      "fullstack": [
        "Senior Fullstack Engineer",
        "Fullstack Engineer",
        "Fullstack JavaScript Developer"
      ],
      "software_engineering": [
        "Software Engineer"
      ],
      "platform": [
        "Platform Engineer"
      ]
    },
    "weights": {
      "title_alignment": 30.0,
      "title_family": 10.0,
      "must_have_skills": 35.0,
      "preferred_skills": 10.0,
      "location_fit": 10.0,
      "remote_fit": 10.0,
      "recency": 5.0,
      "company_preference": 5.0,
      "timezone_fit": 5.0,
      "source_trust": 5.0,
      "feedback_signal": 5.0
    }
  },
  "application": {
    "professional_summary": "Senior Fullstack Engineer with 9 years building and modernizing production web systems across React, TypeScript, Node.js, PostgreSQL, AWS, and Terraform.",
    "key_achievements": [
      "Led a React 17 to 19 migration with Vite and Tailwind v4, splitting a legacy monolith into feature modules and cutting average file size by about 56%.",
      "Shipped in-product analytics (heatmaps, treemaps, stacked bars) and reliability flows for long-running AI chat workloads at DataGPT AI.",
      "Owned CI/CD and Terraform-based infrastructure for a multi-tenant cafe OS (Dumadine), pairing Dockerized releases with Grafana, Prometheus, and Loki."
    ],
    "project_highlights": [
      "Privv (Senior Software Engineer) modernized a construction PM platform frontend with NestJS, TanStack Query, Zustand, Vitest, and a token-driven Tailwind design system.",
      "Dumadine delivered QR ordering, kitchen display, inventory, and loyalty on a multi-tenant stack with WebSockets and idempotent order flows.",
      "DataGPT AI built a node-based Agents V1 workflow UI (data source, prompt, condition, recipient) for automation experiments."
    ],
    "salary_expectation": null,
    "notice_period": null,
    "current_availability": null,
    "remote_preference": null,
    "relocation_preference": null,
    "work_authorization": null,
    "work_hours": null
  }
}
```

## Application packet sample

_No valid normalized job in SQLite or missing `config/app.toml`; packet not built._

## Local template cover letter sample

_Not generated (no packet)._
