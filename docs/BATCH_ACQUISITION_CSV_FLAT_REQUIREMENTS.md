# Batch Acquisition CSV Flat Script Requirements

This document defines functional and non-functional requirements for:

- `scripts/api_scripts/batch_acquisition_csv_flat.py`

## Purpose

Provide a single-file, operator-friendly CLI to start Binalyze AIR evidence acquisition tasks from a CSV host list with strict safety checks and controlled batching.

## Scope

In scope:

- Read host identifiers from CSV.
- Resolve and validate assets in AIR before mutation.
- Create one AIR case.
- Resolve acquisition profile by name and send acquire tasks.
- Enforce endpoint-only task assignment.
- Defer server-class endpoints to the end.
- Require per-server manual approval.
- Print final isolation status table and run summary.

Out of scope:

- Automatic unisolate/remediation.
- Parallel task dispatch.
- Long-running task state orchestration beyond immediate POST response handling.

## Runtime and Dependencies

- Python 3.10+ compatible.
- No additional runtime dependency required for color (ANSI-only).
- Uses:
  - `requests`
  - `python-dotenv`

## Configuration Inputs

- Environment variables:
  - `BINALYZE_AIR_HOST` (or `AIR_HOST`)
  - `BINALYZE_API_TOKEN` (or `AIR_API_TOKEN`)
- CLI arguments:
  - `--org-id` (default `0`)
  - `--csv` (required)
  - `--profile-name` (required)
  - `--case-name` (required)
  - `--hostname-column` (default `name`)
  - `--batch-size` (default `5`, must be `>=1`)
  - `--case-visibility` (`public-to-organization` or `private-to-users`)
  - `--dry-run` (no case/acquire mutation)
  - `--verbose` (extra per-host output)

## Functional Requirements

1. **Startup UX**
   - Show ANSI-colored B!CERT banner with script version.
   - Wait 3 seconds before continuing.

2. **CSV Parsing and Validation**
   - CSV must contain `--hostname-column`.
   - Blank/null hostname cells must fail-fast with row context.
   - Duplicate hostnames may be allowed with warning.

3. **Organization Validation**
   - Validate organization by `GET /api/public/organizations/{org_id}`.
   - Abort on non-2xx.

4. **Asset Resolution**
   - Resolve all identifiers before any case/acquire mutation.
   - Use strict logic:
     - direct asset lookup by ID
     - fallback search by org + query
     - exact name and short-name/FQDN disambiguation
   - Abort if any identifier is unresolved/ambiguous.

5. **Profile Resolution**
   - List profiles from `GET /api/public/acquisitions/profiles` scoped by org.
   - Match by `name` case-insensitively.
   - Map profile argument to acquire profile ID:
     - built-ins use slug (`quick`, `full`, etc.)
     - custom profiles use row `_id`/`id`

6. **Case Creation**
   - Create one case via `POST /api/public/cases` with:
     - `name`
     - `organizationId`
     - `visibility`

7. **Endpoint-only Assignment**
   - Send acquire tasks only for assets whose type is `endpoint`.
   - Skip non-endpoints and report skipped entries.

8. **Server Classification and Ordering**
   - Determine server-class using:
     - primary: `isServer`
     - fallback: tag contains `server` (case-insensitive)
   - Preserve CSV order within groups.
   - Assignment order must be:
     - all non-server endpoints first
     - all server endpoints last

9. **Batching and Operator Gates**
   - Process in batches of N (`--batch-size`).
   - Prompt before each subsequent batch.
   - In server phase, prompt per server endpoint before POST.

10. **Acquire API Call**
    - Use `POST /api/public/acquisitions/acquire`.
    - Body shape:
      - `caseId`
      - `droneConfig`
      - `taskConfig`
      - `acquisitionProfileId`
      - `filter: { name, organizationIds }`

11. **Final Reporting**
    - Always print isolation table for resolved assets.
    - Table includes:
      - Hostname
      - Type
      - Is server (`server` or `-`)
      - Isolation status
      - Latest isolation task
    - Print run summary counters.

## Color and Output Requirements

- ANSI-only colorization (no color library).
- Color disabled automatically when not TTY or `NO_COLOR` is set.
- Current key color rules:
  - org name, endpoint names, profile name, OK states
  - server warnings and server phase header
  - error lines in red
  - isolation table:
    - hostname white
    - non-endpoint type dark gray
    - `isolated` status red

## Error Handling Requirements

- Fail-fast on invalid input, unresolved assets, profile lookup failure, and case create failure.
- Capture and report per-host acquire failures without crashing mid-batch.
- Exit non-zero if any acquire call fails.

## Non-Functional Requirements

- Deterministic ordering for operational predictability.
- Clear interactive prompts for analyst approval points.
- Readable terminal output for SOC workflows.
