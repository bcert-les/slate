# Workflow: Batch CSV Acquisition

Reads a CSV of hostnames, validates each asset, resolves acquisition profiles,
creates or reuses a Binalyze AIR case, and submits acquisition tasks in batches.
Optionally supports a bulk un-isolation step at the end.

## Requirements

- Python 3.9+
- Dependencies: `pip install -r requirements.txt`
- `.env` at the repository root with `BINALYZE_AIR_HOST` and `BINALYZE_API_TOKEN`

## Usage

Run from the repository root:

```bash
python workflows/batch_acquisition_csv/batch_acquisition_csv.py \
  --csv hosts.csv \
  --profile-name "Quick triage" \
  --case-name "Investigation X" \
  --org-id <org_id>
```

### Key options

| Flag | Description |
|------|-------------|
| `--csv PATH` | CSV file with a `name` column of hostnames (required) |
| `--org-id ID` | Binalyze organization ID (or set `BINALYZE_ORG_ID` in `.env`) |
| `--profile-name NAME` | Acquisition profile name (case-insensitive match) |
| `--profile-id ID` | Use exact profile ID instead of name |
| `--case-name NAME` | Name for the new case (auto-generated if omitted) |
| `--case-id ID` | Reuse an existing open case |
| `--batch-size N` | Endpoints per batch (default: 5) |
| `--poll` | Poll each batch's tasks to completion before continuing |
| `--unisolate` | Run bulk un-isolation after acquisition |
| `--dry-run` | Build the plan and print it without sending any write API calls |

### CSV format

The CSV must have at least a `name` column. Extra columns are ignored.

```csv
name
WORKSTATION-01
SERVER-02
LAPTOP-03
```

## Self-contained design

This script intentionally avoids shared-module imports so it can be distributed as a
single file to customers. All HTTP handling (retry, pagination, backoff) is
inlined directly in this file.
