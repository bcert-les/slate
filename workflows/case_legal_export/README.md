# Workflow: Legal case export

Export all DRONE **findings** and every Investigation Hub **evidence table** from a Binalyze AIR case into per-table CSV files, supplemental metadata, a chain-of-custody report, and a ZIP archive suitable for outside counsel review.

## Requirements

- Python 3.9+
- `pip install -r requirements.txt` (from repository root)
- `.env` with:
  - `BINALYZE_AIR_HOST` — your AIR tenant URL
  - `BINALYZE_API_TOKEN` — API bearer token with access to the case

## Quick start

From the repository root:

```bash
python workflows/case_legal_export/case_legal_export.py
```

1. Select an **organization**
2. Select a **case** (any status, including closed)
3. Review the summary and confirm
4. Wait for the export to finish

Optional:

```bash
python workflows/case_legal_export/case_legal_export.py \
  --operator "Jane Analyst" \
  --yes
```

## Output

Each run creates a timestamped folder under `output/`:

```
output/<case_id>_<UTC_timestamp>/
  chain_of_custody.txt
  manifest.json
  csv/
    findings.csv
    evidence_<platform>_<section>.csv
  supplemental/
    case_tasks.csv / .json
    case_endpoints.csv / .json
    findings_summary.json
    flags.json
    comments.json
  .checkpoint.json          # resume state (omit from handoff if desired)
output/<case_id>_<UTC_timestamp>.zip
```

Deliver the **ZIP** (or the folder) plus `chain_of_custody.txt` to counsel.

### Empty evidence tables

Sections with zero rows still get a CSV with column headers (from the evidence data-structure API when available).

## Resume after interruption

If a large export stops mid-run, resume into the same folder:

```bash
python workflows/case_legal_export/case_legal_export.py \
  --output-dir output/C-2026-00001_20260518T120000Z \
  --yes
```

Use `--no-resume` to discard checkpoint progress and start over in that folder.

## Chain of custody

`chain_of_custody.txt` and `manifest.json` include:

- Export start/end times (UTC)
- Script version and dependency versions
- Hostname and OS user running the export
- Operator name (`--operator`, or `not provided`)
- AIR host and organization
- Best-effort API token identity (JWT claims when decodable)
- Case metadata from the API
- Row counts per findings and evidence table
- MD5, SHA-1, and SHA-256 for every file in the package

## Legal handoff checklist

- [ ] Case has completed acquisition (Investigation Hub `investigationId` present)
- [ ] Export completed without API errors in the console log
- [ ] Verify ZIP opens and file count matches `manifest.json`
- [ ] Spot-check hashes: `shasum -a 256` on a sample CSV vs `manifest.json`
- [ ] Store ZIP and custody report with your matter records
- [ ] Restrict access to `.env` credentials used for the export

## Troubleshooting

**`Could not retrieve investigation assets` with an HTTP status (401, 403, 404)**  
Check `BINALYZE_AIR_HOST` and `BINALYZE_API_TOKEN` in `.env`, and that your token can access the case’s organization.

**Warning: Investigation Hub returned no assets**  
The API call succeeded but returned an empty asset list. This is common on **closed** cases or cases where acquisition data was never ingested into Investigation Hub. The export still runs (supplemental files, empty findings/evidence CSVs). Pick a case that shows endpoints and a non-empty investigation in the AIR UI, or verify the case has completed acquisition tasks.

## Disclaimer

This workflow produces a point-in-time export from the AIR API. It does not replace platform-native audit logs or formal e-discovery collection. Consult your legal team on admissibility and retention requirements.
