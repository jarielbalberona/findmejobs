# Profile verification (post-change, evidence-based)

## A) Effective profile source of truth

- Runtime loads `config/profile.yaml` and sibling `config/ranking.yaml` via `load_profile_config` in `src/findmejobs/config/loader.py` (lines 23-27, 63-114).
- Bootstrap drafts use `state/profile_bootstrap/drafts/profile.draft.yaml` via `ProfileBootstrapService.load_profile_draft` in `src/findmejobs/profile_bootstrap/service.py` (lines 177-179).

## B) Profile schema

- Runtime `ProfileConfig` and nested `ApplicationProfile`: `src/findmejobs/config/models.py` (lines 223-257).
- Draft `ProfileConfigDraft` now includes optional `application`: `src/findmejobs/profile_bootstrap/models.py` (lines 30-32).

## C) Field usage map (high level)

- Ranking still uses `target_titles`, skills, `preferred_locations`, and `profile.ranking.*` via `src/findmejobs/ranking/engine.py` (lines 25-41) and `src/findmejobs/ranking/hard_filters.py` (lines 11-46).
- Application packet construction: `src/findmejobs/application/service.py` `_build_application_packet` (from line 522), `_build_strengths` (from line 818), `_build_top_relevant_highlights` (from line 927), `_bounded_packet_text` (from line 1341).
- Cover letter instructions: `src/findmejobs/application/prompts.py` `build_cover_letter_request` (from line 13).

## D) Populated values

- See machine-generated dump in `reports/profile_audit_after.md` (JSON block). Operator `config/profile.yaml` is gitignored; use local file or the sample structure in `config/examples/profile.application.sample.yaml`.

## E) CV coverage

- Baseline role extraction uses `_work_experience_segment` and tightened company cleanup in `src/findmejobs/profile_bootstrap/baseline.py` (`_work_experience_segment`, `_extract_recent_roles`).
- Regression test: `tests/test_profile_bootstrap.py::test_baseline_recent_roles_keeps_privv_after_summary_years_of_experience`.

## F) Root cause (updated)

- Prior gaps: empty `application.key_achievements` / `project_highlights`, ignored `profile.strengths`, fragile `RECENT_ROLE_RE` across summary text, and promote/loader not retaining nested `application` on `ProfileConfigDraft` (addressed by optional `application` on the draft model plus loader coalescing in `loader.py` lines 72-77).

## G) Suggested patches

- None pending; changes are applied on branch `fix/profile-missing-pieces`.
