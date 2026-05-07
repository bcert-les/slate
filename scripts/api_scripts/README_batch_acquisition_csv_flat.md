# README: `batch_acquisition_csv_flat.py`

Script path:

- `scripts/api_scripts/batch_acquisition_csv_flat.py`

## What this script does

Runs a controlled Binalyze AIR acquisition workflow from a CSV host list:

1. Validates org and resolves all assets first.
2. Resolves acquisition profile by name.
3. Creates one case.
4. Sends acquire tasks in batches.
5. Defers server-class endpoints to the end with per-server approval.
6. Prints final isolation table + summary.

## Key behavior

- **Endpoint-only assignment**: tasks are sent only for `assetType/type == endpoint`.
- **Server detection**:
  - Primary: `isServer`
  - Fallback: tag contains `server`
- **Deferred server phase**:
  - Non-server endpoints first
  - Server endpoints last
- **ANSI terminal UX**:
  - B!CERT banner, colorized statuses, and 3-second startup pause.

## Requirements

- Python 3.10+
- Env vars in `.env`:
  - `BINALYZE_AIR_HOST` (or `AIR_HOST`)
  - `BINALYZE_API_TOKEN` (or `AIR_API_TOKEN`)

## Usage

From repo root:

```bash
python scripts/api_scripts/batch_acquisition_csv_flat.py \
  --org-id 362 \
  --csv scripts/api_scripts/hosts.csv \
  --profile-name "Quick" \
  --case-name "IR-2026-05-07"
```

## CLI options

- `--org-id` (default: `0`)
- `--csv` (required)
- `--profile-name` (required)
- `--case-name` (required)
- `--hostname-column` (default: `name`)
- `--batch-size` (default: `5`)
- `--case-visibility` (`public-to-organization` or `private-to-users`)
- `--dry-run`
- `--verbose`

## Acquire API payload used

Endpoint:

- `POST /api/public/acquisitions/acquire`

Payload shape:

```json
{
  "caseId": "C-2026-00042",
  "droneConfig": {"autoPilot": false, "enabled": false},
  "taskConfig": {"choice": "use-policy"},
  "acquisitionProfileId": "quick",
  "filter": {
    "name": "B1-DC01",
    "organizationIds": [362]
  }
}
```

## Isolation table columns

- `Hostname`
- `Type`
- `Is server` (`server` or `-`)
- `Isolation status`
- `Latest iso task`

## Notes

- If output is redirected or `NO_COLOR` is set, ANSI colors are disabled.
- Script exits non-zero if any acquire call fails.
- Non-endpoint assets are resolved and reported but skipped for acquisition.
