# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.2] - 2026-04-21

### Added

- `scripts/test_data_generators/filter_enumerate_assets_output.py` -- filter `enumerate_assets.py` JSON by top-level asset keys (`--include` / `--exclude`); write filtered JSON and/or CSV. README: project tree, usage, cross-note from `enumerate_assets`.

## [0.4.1] - 2026-04-20

### Added

- `scripts/api_scripts/enumerate_assets.py` -- paginate `GET /api/public/assets` for a given organization ID; writes JSON (`organizationId`, `count`, `assets`) and CSV (union of top-level keys, nested values JSON-encoded). Options: `--json`, `--csv`, `--quiet`.

## [0.4.0] - 2026-03-17

### Added

- `scripts/api_scripts/case_acquire.py` -- full end-to-end acquisition workflow via API: find endpoint, select acquisition profile, create or reuse a case, and assign the acquisition task with `POST /acquisitions/assign-task`. Supports `--dry-run` to preview the request, `--poll` to wait for task completion, and interactive or fully-automated profile/case selection.
- README: documented `case_acquire.py` with usage examples and options
- README: added `case_acquire.py` and `docs/DATA_STRUCTURE.md` to project structure tree
- README: added new key endpoints (`POST /cases`, `GET /assets`, `GET /acquisitions/profiles`, `POST /acquisitions/assign-task`) to reference table

## [0.3.1] - 2026-03-17

### Added

- `docs/DATA_STRUCTURE.md` -- visual tree-view of the Binalyze AIR entity hierarchy, key relationships, toolkit data flow, and SQLite output schema

## [0.3.0] - 2026-03-16

### Fixed

- Investigation Hub API paths in `docs/API_README.md` -- added missing `investigations/` segment to all 73 endpoint paths so the reference matches the working API
- `case_extract_findings.py` -- rewrote to probe correct Investigation Hub endpoints instead of non-existent paths; now auto-discovers the investigation ID from the case
- `case_evidence_structure.py` -- moved `_format_size()` from inside a loop to module level
- README: restored missing `.env` filename in project structure tree, restored the env variable example block that was accidentally removed, fixed indentation

### Added

- README: documented `wrkfl_process_analysis.py` (interactive process analysis workflow)
- README: added Troubleshooting section covering common errors (`organizationId required`, `urllib3` warning, missing env vars, Investigation Hub availability, download speed)
- README: added `CHANGELOG.md`, `HARDENING.md`, and `wrkfl_process_analysis.py` to project structure tree
- Consistent error handling (`try/except` with traceback) in `enumerate_orgs.py` and `enumerate_cases.py`

### Changed

- `case_extract_findings.py` now looks up the investigation ID from the case and probes both case endpoints and Investigation Hub endpoints with correct paths

## [0.2.0] - 2026-03-16

### Fixed

- Investigation Hub API paths in `case_evidence_structure.py` -- added missing `investigations/` path segment so hub endpoints return data instead of silently failing
- Case lookup in `case_evidence_structure.py` -- now enumerates all organizations when no `org_id` is provided, since the cases endpoint requires an `organizationId` filter

### Changed

- Project renamed from `b-_threat_hunt_poc` to `slate`
- `get_case_by_investigation_id()` now uses `paginate_get()` to search through all cases, not just the first 100

## [0.1.0] - 2026-03-13

### Added

- Initial release migrated from hackathon project
- `enumerate_orgs.py` -- list all organizations in a Binalyze AIR tenant
- `enumerate_cases.py` -- list cases for an organization, filtered by status
- `case_findings.py` -- extract detailed findings (acquisitions, triage tasks) from a case
- `case_evidence_structure.py` -- show evidence structure for an investigation
- `case_download_evidence.py` -- download parsed evidence data from the Investigation Hub with streaming writes, deduplication, checkpoint/resume, and retry with backoff
- `case_extract_findings.py` -- probe multiple API endpoints to discover available findings
- `lib/api_client.py` -- shared HTTP client with Bearer auth, retry with exponential backoff on 429/5xx, and `Retry-After` support
- `lib/pagination.py` -- paginated GET helper for standard Binalyze response shapes
- SQLite output with streaming writes, dedup via unique index, and checkpoint/resume
- CSV and JSON output formats
- Documentation: API reference (`docs/API_README.md`), scalability analysis (`docs/SCALABILITY.md`), hardening notes (`docs/HARDENING.md`)
