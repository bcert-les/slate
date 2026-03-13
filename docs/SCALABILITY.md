# Scalability Analysis: 10,000 Endpoint Weekly Collection

Stress-test of the `case_download_evidence.py` workflow against a realistic large-customer scenario: 10,000 Windows endpoints, weekly forensic evidence collection.

## Baseline Numbers

Per-endpoint averages derived from lab data (6 endpoints, 2 investigations):

| Metric | Value |
|---|---|
| Avg processes per endpoint | ~130 |
| Avg CSV row size | ~555 bytes |
| Columns per process row | 38 |

## Processes at 10k Endpoints (~1.3M rows)

| Stage | Math | Concern |
|---|---|---|
| **API calls** | 1.3M rows / 500 per page = **2,600 POST requests** | Throttling risk |
| **Download time** | ~1-2s per request = **43-87 minutes** | Acceptable but slow |
| **Memory** | 1.3M rows x 38 cols x ~50 bytes avg = **~2.5 GB in RAM** | All rows loaded before write |
| **SQLite write** | 1.3M rows in one transaction = seconds | Fine |
| **DB size per run** | ~1.3M rows x ~600 bytes/row = **~780 MB** | Fine |

## Weekly Accumulation Over Time (Processes Only)

| Timeframe | Rows | DB Size |
|---|---|---|
| Week 1 | 1.3M | ~780 MB |
| Month 1 | 5.2M | ~3.1 GB |
| Month 6 | 33.8M | ~20 GB |
| Year 1 | 67.6M | ~40 GB |

## Heavier Evidence Categories at 10k Scale

| Category | Per endpoint | 10k endpoints | Memory needed |
|---|---|---|---|
| processes | ~130 | 1.3M | ~2.5 GB |
| dns_cache | ~664 | 6.6M | ~5 GB |
| autoruns_services | ~643 | 6.4M | ~5 GB |
| event_records_details | ~93,000 | **930M** | **~180 GB -- impossible** |
| processes_handles | ~54,000 | **540M** | **~100 GB -- impossible** |

---

## Identified Problems and Mitigations

### 1. API Throttling (HIGH RISK)

**Problem:** 2,600+ sequential API requests with no backoff. If Binalyze returns 429 (Too Many Requests) or 5xx, the script treats it as an error and stops -- partial data loss mid-download.

**Mitigation:**
- Retry with exponential backoff on 429 / 5xx responses
- Respect `Retry-After` headers if present
- Configurable request delay between calls (default 100ms)
- Max retry count to avoid infinite loops

### 2. Memory Exhaustion (HIGH RISK)

**Problem:** Script accumulates every row in a Python list before writing to SQLite. At 1.3M rows that's ~2.5 GB. At 6.6M rows (dns_cache) it will crash most machines. The heavier categories (event_records_details, processes_handles) are flatly impossible.

**Mitigation:**
- Stream pages directly to SQLite as they arrive
- Write each batch of 500 rows immediately, don't accumulate
- Peak memory usage drops from O(total_rows) to O(page_size)

### 3. No Resume / Checkpoint (MEDIUM RISK)

**Problem:** If the script fails at row 800k of 1.3M, you restart from zero. At 43-87 minutes per run, that's painful and wastes API quota.

**Mitigation:**
- Track progress in a `_checkpoints` table in the SQLite database
- On restart, read last successful `skip` offset and resume from there
- Checkpoint updated after each successful page write

### 4. No Deduplication (MEDIUM RISK)

**Problem:** Running the script twice for the same investigation + category appends duplicate rows. Over weekly runs, the DB doubles in size unless manually managed.

**Mitigation:**
- Composite unique index on (`air_id`, `air_task_assignment_id`)
- Use `INSERT OR IGNORE` to silently skip duplicates
- Re-running is now safe and idempotent

### 5. SQLite at 40-67M Rows (LOW-MEDIUM RISK)

**Problem:** SQLite technically handles this volume, but:
- Queries without indexes table-scan 67M rows (minutes, not milliseconds)
- No time-based partitioning -- querying "last week's data" scans everything
- Single-writer lock blocks concurrent ingest + query (WAL mode helps but doesn't fully solve)
- `VACUUM` after deletes takes hours on a 40 GB file

**Mitigation:**
- Add `ingested_at` timestamp column with index for time-range queries
- Consider separate DB files per week or per investigation at extreme scale
- Move to DuckDB if analytical query performance becomes a bottleneck past ~20M rows

### 6. Single-Threaded Download (LOW RISK)

**Problem:** One request at a time. Could parallelize across endpoints or evidence categories.

**Mitigation:** Not critical for processes at 10k. Parallelism adds complexity and worsens throttling risk. Can be revisited if download time becomes the bottleneck.

---

## Verdict

For **processes at 10k endpoints, weekly**, the script is viable after fixing memory (streaming writes) and throttling (retry/backoff). Without those fixes, expect OOM or rate-limit failures with partial data loss.

For anything heavier than processes/dns_cache at 10k scale, a fundamentally different architecture is needed (streaming + DuckDB or PostgreSQL).
