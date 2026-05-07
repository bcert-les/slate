# Workflow: Process Analysis

Interactive workflow for Windows process frequency analysis. Connects to Binalyze AIR,
lets you pick an open case, downloads all Windows process evidence to SQLite, then
prints the top-10 (most common) and bottom-10 (rarest) processes by frequency.

**The bottom-10 are the hunting gold** — rare processes that may indicate compromise.

## Requirements

- Python 3.9+
- `pip install -r requirements.txt`
- `.env` with `BINALYZE_AIR_HOST`, `BINALYZE_API_TOKEN`, and `BINALYZE_ORG_ID`

```env
BINALYZE_AIR_HOST=https://your-tenant.binalyze.com
BINALYZE_API_TOKEN=api_your_token_here
BINALYZE_ORG_ID=362
```

## Usage

Run from the repository root:

```bash
python workflows/process_analysis/process_analysis.py
```

The workflow:
1. Fetches open cases for your organization
2. Presents an interactive menu to select a case
3. Downloads Windows process evidence (streaming to SQLite, resumable)
4. Prints summary: total rows, endpoints, unique process count
5. Prints top-10 (most common) and bottom-10 (rarest) processes

Output is written to `output/evidence.db` with a timestamped table per run
(e.g. `processes_MyCase_20260507_143012`).
