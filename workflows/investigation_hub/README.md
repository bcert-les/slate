# Workflow: Investigation Hub

Scripts for exploring and downloading forensic evidence through the Binalyze AIR
Investigation Hub API. Intended for cases that have completed at least one acquisition.

## Scripts

| Script | Description |
|--------|-------------|
| `case_findings.py` | Display acquisitions and triage tasks for a case |
| `evidence_structure.py` | Full evidence structure report: endpoints, tasks, Hub API data |
| `extract_findings.py` | Probe all case and Hub endpoints; discover what data is available |
| `download_evidence.py` | Stream evidence rows to SQLite (with checkpoint/resume) or export as CSV/JSON |

## Requirements

- Python 3.9+
- `pip install -r requirements.txt`
- `.env` with `BINALYZE_AIR_HOST` and `BINALYZE_API_TOKEN`

## Typical sequence

```bash
# 1. Find your investigation ID (get it from list_cases or case_findings)
python api/list_cases.py <org_id>

# 2. Browse what evidence exists for an investigation
python workflows/investigation_hub/download_evidence.py <investigation_id> --list

# 3. Download process data to SQLite (streaming, resumable)
python workflows/investigation_hub/download_evidence.py <investigation_id> processes

# 4. Export as CSV instead
python workflows/investigation_hub/download_evidence.py <investigation_id> processes --format csv

# 5. Probe all endpoints to see what data a case has
python workflows/investigation_hub/extract_findings.py <org_id> <case_id>
```

## Download options

```bash
# Specify SQLite database path
python workflows/investigation_hub/download_evidence.py <inv_id> processes \
  --db output/my_case.db

# Download both SQLite and CSV
python workflows/investigation_hub/download_evidence.py <inv_id> processes --format all

# Limit rows (useful for testing)
python workflows/investigation_hub/download_evidence.py <inv_id> processes --limit 500

# Ignore checkpoint (fresh download)
python workflows/investigation_hub/download_evidence.py <inv_id> processes --no-resume

# Linux/macOS asset data
python workflows/investigation_hub/download_evidence.py <inv_id> processes --platform linux
```

## SQLite output

Evidence is written to `output/evidence.db` by default (one table per evidence
category). Columns are inferred from API fields; a unique index on
`(air_id, air_task_assignment_id)` prevents duplicates. An `ingested_at` timestamp
and `air_endpoint_name` enrichment column are added to every row.

Interrupted downloads resume automatically from the last checkpoint. Use
`--no-resume` to start fresh.
