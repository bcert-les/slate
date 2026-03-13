# Script Hardening: Production-Ready Evidence Collection

Summary of changes made to `case_download_evidence.py` and `lib/api_client.py` to support large-scale, reliable evidence collection (10,000+ endpoints, weekly runs).

## Changes Overview

### lib/api_client.py -- Retry with Exponential Backoff

All API calls (`api_get`, `api_post`) now automatically retry on transient failures:

| Parameter | Value |
|---|---|
| Retryable status codes | 429, 500, 502, 503, 504 |
| Max retries | 5 |
| Initial backoff | 1.0s |
| Backoff multiplier | 2x per retry |
| Retry-After header | Respected when present |
| Connection/timeout errors | Retried with same backoff |

Fully backwards-compatible -- all existing scripts (`enumerate_orgs.py`, `enumerate_cases.py`, etc.) inherit retry behavior with no code changes.

### scripts/case_download_evidence.py -- Six Production Features

#### 1. Streaming Writes

**Before:** All rows accumulated in a Python list, then written to SQLite in one shot. At 1.3M rows (10k endpoints, processes), this consumed ~2.5 GB of RAM. Heavier categories like `dns_cache` (6.6M rows) or `event_records_details` (930M rows) would crash.

**After:** Each API page (500 rows) is written to SQLite immediately after download. Peak memory usage is O(page_size) -- roughly 1-2 MB regardless of total dataset size.

#### 2. Deduplication

**Before:** Running the script twice for the same investigation + category appended duplicate rows. Weekly runs would double the DB size each time.

**After:** A unique composite index on `(air_id, air_task_assignment_id)` is created on each evidence table. Inserts use `INSERT OR IGNORE` so duplicate rows are silently skipped. Re-running is safe and idempotent. The script reports how many duplicates were skipped per page.

#### 3. Checkpoint / Resume

**Before:** If the script failed at row 800k of 1.3M, you restarted from zero. At 43-87 minutes per run, that wastes significant time and API quota.

**After:** A `_checkpoints` table in the SQLite database tracks the last successful `skip` offset per `(table_name, investigation_id)`. On restart, the script reads the checkpoint and resumes from where it left off. Use `--no-resume` to force a fresh download.

#### 4. ingested_at Timestamp

Every row now includes an `ingested_at` column with the UTC timestamp of when it was written to the database. An index is created on this column automatically.

This enables time-range queries:

```sql
SELECT * FROM processes WHERE ingested_at > '2026-02-16T00:00:00';
```

Useful for comparing weekly snapshots or debugging when specific data was collected.

#### 5. Configurable Request Delay

New `--delay` flag (default: 0.1 seconds) adds a pause between API requests. This reduces the risk of triggering rate limits during large downloads.

```bash
# Slower, safer for very large downloads
python3 scripts/case_download_evidence.py <inv_id> processes --delay 0.5

# No delay (fastest, higher throttle risk)
python3 scripts/case_download_evidence.py <inv_id> processes --delay 0
```

#### 6. Retry (inherited from api_client.py)

All POST calls to the Investigation Hub evidence endpoint now automatically retry on 429/5xx. Combined with the checkpoint system, this means a temporary API outage during a long download results in a brief pause, not a full restart.

## New CLI Flags

| Flag | Default | Description |
|---|---|---|
| `--delay SECONDS` | `0.1` | Pause between API requests |
| `--no-resume` | off | Ignore checkpoint, download from scratch |
| `--db PATH` | `output/evidence.db` | SQLite database path |
| `--limit N` | all | Max rows to download |
| `--format FORMAT` | `sqlite` | Output format: sqlite, json, csv, both, all |

## SQLite Schema Details

### Evidence Tables (one per category)

Columns are dynamically inferred from API response data, plus two enrichment columns:

| Column | Type | Source |
|---|---|---|
| `air_endpoint_name` | TEXT | Resolved from asset data |
| `ingested_at` | TEXT | UTC ISO timestamp |
| *(all other columns)* | *(inferred)* | API response fields |

### Indexes Created

| Index | Purpose |
|---|---|
| `uq_<table>_dedup` | Unique on `(air_id, air_task_assignment_id)` -- dedup |
| `idx_<table>_air_endpoint_name` | Query by endpoint name |
| `idx_<table>_name` | Query by process/artifact name |
| `idx_<table>_air_endpoint_id` | Query by endpoint ID |
| `idx_<table>_ingested_at` | Time-range queries |

### _checkpoints Table

| Column | Type | Description |
|---|---|---|
| `table_name` | TEXT (PK) | Evidence category |
| `investigation_id` | TEXT | Investigation UUID |
| `last_skip` | INTEGER | Last successful offset |
| `total_count` | INTEGER | Total rows reported by API |
| `updated_at` | TEXT | When checkpoint was saved |

## SQLite Pragmas

The writer enables two performance pragmas:

- **`PRAGMA journal_mode=WAL`** -- Write-Ahead Logging for better concurrent read/write performance
- **`PRAGMA synchronous=NORMAL`** -- Slightly faster writes with acceptable durability (data safe on OS crash, not on power loss mid-write)
