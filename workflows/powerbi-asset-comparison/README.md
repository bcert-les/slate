# Workflow: Power BI Asset Comparison

A Power Query M script that pulls the Binalyze AIR asset inventory directly into
Power BI (or Excel) for visual comparison and reporting. Hits
`GET /api/public/assets` in a single request with a large `pageSize`,
deduplicates by `_id`, and returns a trimmed, analysis-friendly table.

## Files

| File | Purpose |
|------|---------|
| `asset_comparison.pq` | Power Query M source. Paste into Power BI's Advanced Editor. |

## Output schema

| Column | Type | Notes |
|--------|------|-------|
| `name` | text | Endpoint hostname |
| `onlineStatus` | text | `online`, `offline`, etc. |
| `netInterfaces` | text | JSON-stringified array of interface objects (so it survives Power BI table loads) |
| `lastSeen` | datetime | Last AIR check-in |
| `createdAt` | datetime | When the asset was first registered |

## Setup

1. Open **Power BI Desktop** -> **Home** -> **Get data** -> **Blank Query**.
2. Open the **Advanced Editor**.
3. Paste the contents of `asset_comparison.pq`.
4. Replace the three placeholders at the top:
   - `<AIR_HOST>` -- e.g. `https://your-tenant.binalyze.com`
   - `<API_TOKEN>` -- a Binalyze API token (treat like a password)
   - `<ORG_ID>` -- target organization ID (e.g. `362`)
5. Click **Done** and rename the query (e.g. "AIR Assets").
6. Click **Close & Apply**.

To find your org ID, run `python api/list_organizations.py` from the repo root.

## How the request works

- Single `GET /api/public/assets?filter[organizationIds]=<ORG_ID>&page=1&pageSize=10000` call.
- `Accept: application/json` so AIR returns the standard wrapped response.
- Defensive unwrapping: tries `result.entities` first, falls back to a top-level array.
- Dedup on `_id` afterwards.
- `netInterfaces` is JSON-stringified so Power BI can render the column.

`pageSize=10000` is an upper bound — adjust if your tenant has more assets,
or revert to a paginated request if it grows past that.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `401 Unauthorized` | Token doesn't have access to the tenant or org, or Power BI's data-source auth is overriding the manual `Authorization` header | Issue a valid token; in Power BI go to **File** → **Options and settings** → **Data source settings** and set the data source's auth method to **Anonymous** so the manual header is honored |
| Empty table after refresh | Power BI is showing a cached empty result, or the org has no assets | **File** → **Options** → **Current File** → **Data Load** → **Clear Cache**, then **Home** → **Refresh** |
| Truncated to 10,000 rows | Tenant has more assets than `PageSize` | Raise `PageSize` in the script, or restore the paginated version from git history |

## Security notes

**Do not commit the `.pq` file with real credentials.** The Power Query
language has no equivalent of a `.env` file, so the host/token/org ID live
inline in the script. Two safer alternatives:

- **Power BI parameters** -- replace each placeholder with a parameter reference
  (`Host = Text.TrimEnd(HostParam, "/")`) and store the values in the report's
  parameters pane. Parameters travel with the `.pbix` file but can be
  overridden at refresh time in the Power BI service.
- **Personal credential split** -- keep one shared `.pq` template with
  placeholders in version control, and a separate uncommitted local copy with
  the real values pasted in.

API tokens carry the same access as the user that issued them; rotate the
token in AIR if you suspect exposure.

## Status

Initial draft. The script will likely be updated to:

- Pull additional asset fields once the comparison view is finalized.
- Optionally split `netInterfaces` into separate rows or columns.
- Add a related table for isolation status / tasks for joins in Power BI.
