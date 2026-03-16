# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
