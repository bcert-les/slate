# Workflow: Power BI Asset Comparison

A Power Query M script that pulls the full Binalyze AIR asset inventory directly
into Power BI (or Excel) for visual comparison and reporting. Uses a paginated
loop over `GET /api/public/assets` (1,000 records per request), deduplicates by
`_id`, and returns a trimmed, analysis-friendly table. Safe for tenants of any
size — the previous single-shot approach hit a server-side `400 Bad Request`
when `pageSize` exceeded 10,000.

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

- `GetPage(n)` fetches `GET /api/public/assets?filter[organizationIds]=<ORG_ID>&page=n&pageSize=1000`.
- Page 1 is fetched first to read `totalPageCount` from the response.
- `List.Generate` loops from page 1 to `totalPageCount`, stopping early if a page returns no entities.
- All page results are combined with `List.Combine` before dedup and column trimming.
- `Accept: application/json` so AIR returns the standard wrapped response.
- Defensive unwrapping: tries `result.entities` first, falls back to the root object.
- Dedup on `_id` after combining all pages.
- `netInterfaces` is JSON-stringified so Power BI can render the column.

`PageSize = 1000` keeps each request well under the server-enforced 10,000 cap
(sending `pageSize=20000` returns `400 Bad Request`). Raise it toward 5,000 if
you want fewer round-trips, but do not exceed 10,000.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `401 Unauthorized` | Token doesn't have access to the tenant or org, or Power BI's data-source auth is overriding the manual `Authorization` header | Issue a valid token; in Power BI go to **File** → **Options and settings** → **Data source settings** and set the data source's auth method to **Anonymous** so the manual header is honored |
| Empty table after refresh | Power BI is showing a cached empty result, or the org has no assets | **File** → **Options** → **Current File** → **Data Load** → **Clear Cache**, then **Home** → **Refresh** |
| `400 Bad Request` on refresh | `pageSize` exceeds the server cap (~10,000) | Lower `PageSize` in the script — the default of 1,000 is safe |
| Fewer rows than expected | `totalPageCount` was not returned by the API | The loop falls back to 1 page; check the raw API response for the actual pagination field name |

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

Paginated — works for tenants of any size. Potential future improvements:

- Pull additional asset fields once the comparison view is finalized.
- Optionally split `netInterfaces` into separate rows or columns.
- Add a related table for isolation status / tasks for joins in Power BI.
