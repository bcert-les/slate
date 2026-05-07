# Updraft — Binalyze AIR API Toolkit

**Version:** 0.5.0

Python scripts for interacting with the Binalyze AIR API. Enumerate organizations,
cases, and assets; download forensic evidence; orchestrate isolation and acquisition
workflows.

## Project Structure

```
updraft/
  .env                        # API credentials (not committed)
  requirements.txt
  CHANGELOG.md
  api/                        # One-endpoint example CLIs
    README.md                 # Script index with METHOD + route
    list_organizations.py
    get_organization.py
    list_cases.py
    get_case.py
    post_case.py
    list_case_tasks.py
    list_assets.py
    export_assets.py
    post_assets_filter.py
    list_asset_tasks.py
    list_acquisition_profiles.py
    post_acquisitions_acquire.py
    get_task.py
    post_isolation_task.py
  workflows/                  # Multi-step orchestration
    batch_acquisition_csv/    # Batch acquire from CSV host list
    isolation_xsoar/          # Binalyze + XSOAR isolation workflow
    acquire_evidence/         # Single-endpoint acquisition workflow
    investigation_hub/        # Evidence structure, download, findings
    process_analysis/         # Interactive Windows process frequency analysis
  lib/                        # Shared library modules
    api_client.py             # HTTP helpers, auth, retry with backoff
    pagination.py             # Paginated GET/POST helpers
    binalyze_acquisitions.py
    binalyze_asset_filter.py
    binalyze_cases.py
    binalyze_evidence.py      # SQLite streaming writer, Investigation Hub helpers
    binalyze_isolation.py
    workflow_policy.py
    xsoar_adapter.py
  tools/                      # Standalone data-processing utilities
    filter_assets_output.py   # Filter list_assets.py JSON by field
  config/
    workflow_isolation.example.json
  docs/
    API_README.md
    BATCH_ACQUISITION_CSV_FLAT_REQUIREMENTS.md
    DATA_STRUCTURE.md
    HARDENING.md
    SCALABILITY.md
  test_data/
  output/                     # Data outputs — CSV, JSON, SQLite (gitignored)
```

## Setup

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure environment:**
   Create a `.env` file in the project root:
   ```env
   BINALYZE_AIR_HOST=https://your-tenant.binalyze.com
   BINALYZE_API_TOKEN=api_your_token_here
   BINALYZE_ORG_ID=362          # required by workflows/process_analysis only
   ```

All scripts are run from the project root.

## api/ — Single-endpoint examples

Each script documents and invokes one HTTP route. See [`api/README.md`](api/README.md)
for the full index.

```bash
# Discover your org ID
python api/list_organizations.py

# List open cases
python api/list_cases.py <org_id>

# List endpoints
python api/list_assets.py <org_id>

# Create a case
python api/post_case.py <org_id> --name "Investigation X"

# Check isolation status for a specific endpoint
python api/list_asset_tasks.py <hostname> <org_id>
```

## workflows/ — Multi-step orchestration

### batch_acquisition_csv

Reads a CSV of hostnames, validates assets, selects a profile, creates a case,
and submits acquisition tasks in batches with server-asset gating.

```bash
python workflows/batch_acquisition_csv/batch_acquisition_csv.py \
  --csv hosts.csv \
  --profile-name "Quick triage" \
  --case-name "Investigation X" \
  --org-id <org_id>
```

### isolation_xsoar

Interactive Binalyze + XSOAR isolation workflow with batch gating and audit log.

```bash
python workflows/isolation_xsoar/isolation_xsoar.py <org_id> HOST1 HOST2 --skip-xsoar
python workflows/isolation_xsoar/isolation_xsoar.py <org_id> HOST1 --dry-run
```

### acquire_evidence

Replicate the console acquisition flow: find endpoint, pick profile, create case, POST acquire.

```bash
python workflows/acquire_evidence/acquire_evidence.py <org_id> WORKSTATION-01
python workflows/acquire_evidence/acquire_evidence.py <org_id> WORKSTATION-01 \
  --profile-name "Full" --poll
```

### investigation_hub

Download forensic evidence to SQLite (streaming, resumable) or CSV/JSON.

```bash
# Browse available evidence sections
python workflows/investigation_hub/download_evidence.py <investigation_id> --list

# Stream to SQLite (default)
python workflows/investigation_hub/download_evidence.py <investigation_id> processes

# Export as CSV
python workflows/investigation_hub/download_evidence.py <investigation_id> tcp_table --format csv

# Show full evidence structure for an investigation
python workflows/investigation_hub/evidence_structure.py <investigation_id> [org_id]

# Display case acquisitions and triage tasks
python workflows/investigation_hub/case_findings.py <org_id> <case_id>
```

### process_analysis

Interactive Windows process frequency analysis — top-10 and bottom-10 processes.
The bottom-10 are rare processes that may indicate compromise.

```bash
python workflows/process_analysis/process_analysis.py
```

Requires `BINALYZE_ORG_ID` in `.env`.

## tools/

### filter_assets_output.py

Post-process JSON from `api/list_assets.py`: keep only chosen top-level fields
(`--include`) or drop fields (`--exclude`).

```bash
python tools/filter_assets_output.py output/assets_org_362.json \
  --include _id,name,ipAddress,platform,os \
  --json-out output/assets_core.json \
  --csv-out output/assets_core.csv
```

## API Reference

See [`docs/API_README.md`](docs/API_README.md) for the full endpoint reference.

Key endpoints used:

| Endpoint | Description |
|----------|-------------|
| `GET /api/public/organizations` | List organizations |
| `GET /api/public/cases` | List/filter cases |
| `POST /api/public/cases` | Create a case |
| `GET /api/public/cases/{id}/tasks` | Case tasks |
| `GET /api/public/assets` | List/search assets |
| `GET /api/public/acquisitions/profiles` | Acquisition profiles |
| `POST /api/public/acquisitions/acquire` | Assign acquisition |
| `POST /api/public/assets/tasks/isolation` | Enable/disable isolation |
| `POST /api/public/assets/filter` | Server-side asset filter |
| `POST /api/public/investigation-hub/investigations/{id}/sections` | Evidence sections |
| `POST /api/public/investigation-hub/investigations/{id}/platform/{p}/evidence-category/{c}` | Download evidence |

## Troubleshooting

**`organizationId(s) is required`** — The `/api/public/cases` endpoint requires an org ID
filter. Run `python api/list_organizations.py` to find yours.

**`urllib3 v2 only supports OpenSSL 1.1.1+` warning** — Harmless on macOS with LibreSSL.

**`Set BINALYZE_AIR_HOST and BINALYZE_API_TOKEN in .env`** — Create a `.env` file at the
project root (see Setup above).

**`Set BINALYZE_ORG_ID in .env`** — Only `workflows/process_analysis` requires this.

**`Investigation Hub API not available on this tenant`** — Investigation Hub endpoints
returned no data. The investigation may still be importing, or your token lacks
Investigation Hub permissions, or the case has no completed acquisitions.

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for the full release history.
