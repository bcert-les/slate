# Binalyze AIR API Toolkit

Python scripts for interacting with the Binalyze AIR API -- enumerate organizations, cases, and download forensic evidence data.

## Project Structure

```
hackathon/
  .env                  # API credentials (not committed)
  requirements.txt      # Python dependencies
  lib/                  # Shared library code
    api_client.py       # HTTP helpers, auth, retry w/ backoff
    pagination.py       # Paginated GET helper
  scripts/              # Runnable scripts
    enumerate_orgs.py
    enumerate_cases.py
    case_findings.py
    case_evidence_structure.py
    case_download_evidence.py
    case_extract_findings.py
  output/               # Data outputs -- CSV, JSON, SQLite (gitignored)
  docs/                 # Documentation
    API_README.md       # API endpoint reference
    SCALABILITY.md      # 10k endpoint scale analysis
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
   ```

## Scripts

All scripts are run from the project root.

### enumerate_orgs.py

Lists all organizations in your Binalyze tenant.

```bash
python3 scripts/enumerate_orgs.py
```

### enumerate_cases.py

Lists cases for an organization, filtered by status.

```bash
python3 scripts/enumerate_cases.py <org_id> [status]
```

- `status` defaults to `open`. Use `closed` for closed cases.

### case_findings.py

Extracts detailed findings (acquisitions, triage tasks) from a case.

```bash
python3 scripts/case_findings.py <org_id> <case_id>
```

Output saved to `output/case_findings_<org_id>_<case_id>.json`.

### case_evidence_structure.py

Shows the evidence structure for an investigation, including endpoints, tasks, and collected artifacts.

```bash
python3 scripts/case_evidence_structure.py <investigation_id> [org_id]
```

Output saved to `output/evidence_structure_<id>.json`.

### case_download_evidence.py

Downloads parsed evidence data rows from the Investigation Hub (e.g., processes, network connections, event logs). Hardened for large-scale collection with streaming writes, deduplication, resume/checkpoint, and retry with backoff.

```bash
# List available evidence sections
python3 scripts/case_download_evidence.py <investigation_id> --list

# Download to SQLite (default) -- streams rows, deduplicates, checkpoints
python3 scripts/case_download_evidence.py <investigation_id> processes

# Resume an interrupted download (automatic -- uses checkpoint)
python3 scripts/case_download_evidence.py <investigation_id> processes

# Force fresh download, ignoring checkpoint
python3 scripts/case_download_evidence.py <investigation_id> processes --no-resume

# Custom DB path, slower request rate
python3 scripts/case_download_evidence.py <investigation_id> processes --db output/my_case.db --delay 0.5

# CSV/JSON output (in-memory, per-endpoint files)
python3 scripts/case_download_evidence.py <investigation_id> tcp_table --format csv --limit 100
```

**SQLite output** goes to `output/evidence.db` (one table per evidence category). **CSV/JSON output** is split per-endpoint into `output/evidence_<category>_<endpoint>.[csv|json]`.

Production features:
- **Streaming writes**: each API page is written to SQLite immediately (memory = O(page_size), not O(total))
- **Dedup**: unique index on `(air_id, air_task_assignment_id)` with `INSERT OR IGNORE`
- **Checkpoint**: resume interrupted downloads from last successful page
- **Retry**: exponential backoff on 429/5xx with `Retry-After` support
- **Request delay**: configurable `--delay` (default 0.1s) to avoid throttling
- **ingested_at**: UTC timestamp on every row for time-range queries

### case_extract_findings.py

Probes multiple API endpoints to discover available findings for a case.

```bash
python3 scripts/case_extract_findings.py <org_id> <case_id>
```

## Typical Workflow

```bash
# 1. Find your organization
python3 scripts/enumerate_orgs.py

# 2. List cases in that org
python3 scripts/enumerate_cases.py 362

# 3. Get the investigation ID from a case, then list available evidence
python3 scripts/case_download_evidence.py <investigation_id> --list

# 4. Download specific evidence
python3 scripts/case_download_evidence.py <investigation_id> processes
```

## API Reference

See [docs/API_README.md](docs/API_README.md) for the full list of Binalyze AIR API endpoints (reverse-engineered from the official TypeScript SDK).

Key endpoints used:

| Endpoint | Description |
|---|---|
| `GET /api/public/organizations` | List organizations |
| `GET /api/public/cases` | List/filter cases |
| `GET /api/public/cases/{id}/tasks` | Get case tasks |
| `POST /api/public/investigation-hub/investigations/{id}/sections` | List evidence sections |
| `POST /api/public/investigation-hub/investigations/{id}/platform/{p}/evidence-category/{c}` | Download evidence data |
