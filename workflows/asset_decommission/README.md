# Workflow: Asset Decommission

Compare a customer-supplied "decommissioned hosts" CSV (**A-list**) against the
live Binalyze AIR asset inventory (**B-list**) for an organization, then
uninstall matching endpoints via the AIR API to release license seats.

Typical use case: a customer rotates hardware on a regular basis and the
decommissioned systems were never properly removed from AIR. They appear as
managed assets that have not checked in for 30 or more days. This workflow
identifies those stale entries and removes them in bulk.

## Requirements

- Python 3.9+
- `pip install -r requirements.txt` (from repository root)
- `.env` with:
  - `BINALYZE_AIR_HOST` — your AIR tenant URL, e.g. `https://your-tenant.binalyze.com`
  - `BINALYZE_API_TOKEN` — API bearer token with asset management permissions

## Quick start

From the repository root:

```bash
python workflows/asset_decommission/asset_decommission.py --a-list decommissioned.csv
```

1. Select an **organization** (or pass `--org-id`)
2. Review the **comparison table** — matches and hostnames not found in AIR
3. Confirm the **uninstall prompt**
4. The script calls the AIR uninstall API and prints the final asset counts

### Common invocations

```bash
# Interactive org selection, manual confirmation
python workflows/asset_decommission/asset_decommission.py \
  --a-list decommissioned.csv

# Specify org, skip confirmation
python workflows/asset_decommission/asset_decommission.py \
  --a-list decommissioned.csv \
  --org-id 362 \
  --yes

# Preview only — no API writes
python workflows/asset_decommission/asset_decommission.py \
  --a-list decommissioned.csv \
  --dry-run

# Purge evidence data as well as uninstalling the agent
python workflows/asset_decommission/asset_decommission.py \
  --a-list decommissioned.csv \
  --purge \
  --yes
```

## A-list CSV format

The A-list is a CSV exported from any asset management platform that contains
hostnames. The script **auto-detects** the hostname column by checking for
common header names (case-insensitive):

```
hostname, host_name, host, name, computer_name, computername,
device_name, devicename, endpoint, asset
```

Minimal example:

```csv
hostname,os,department
WORKSTATION-01,Windows 11,Finance
WORKSTATION-02,Windows 10,Finance
LAPTOP-99,Windows 11,IT
```

If the auto-detection fails, specify the column explicitly:

```bash
python workflows/asset_decommission/asset_decommission.py \
  --a-list decommissioned.csv \
  --hostname-column "Device Name"
```

## CLI reference

| Flag | Default | Description |
|------|---------|-------------|
| `--a-list PATH` | prompted | Path to the A-list CSV |
| `--hostname-column COL` | auto-detect | Column header for hostnames |
| `--org-id ID` | interactive | Skip interactive organization selection |
| `--purge` | off | Use `purge-and-uninstall` (see warning below) |
| `--dry-run` | off | Show comparison results without making any API calls |
| `--yes` | off | Skip the confirmation prompt |

## Output

```
======================================================================
  Binalyze AIR — Asset Decommission  (v1.0.0)
======================================================================

Organization : Acme Corp  (ID: 362)

[A-list]  Loaded 47 hostnames from 'decommissioned.csv'  (column: 'hostname')
[B-list]  Fetched 312 assets from org 'Acme Corp'

──────────────────────────────────────────────────────────────────────
COMPARISON RESULTS
──────────────────────────────────────────────────────────────────────

Matches — found in AIR and will be uninstalled (34):
  WORKSTATION-01                 id=abc123  last seen=2026-03-10  status=offline
  WORKSTATION-02                 id=def456  last seen=2026-02-28  status=offline
  ...

Not found in AIR — already removed or never registered (13):
  LAPTOP-99
  ...

──────────────────────────────────────────────────────────────────────
SUMMARY
──────────────────────────────────────────────────────────────────────
  Assets before removal : 312
  Matches to uninstall  : 34
  Not found in AIR      : 13

Uninstall 34 endpoint(s) [uninstall (preserve evidence)]? [y/N]: y

Uninstalling 34 endpoint(s)... done (34 / 34)

Waiting for AIR to process removal... done

──────────────────────────────────────────────────────────────────────
REMOVAL COMPLETE
──────────────────────────────────────────────────────────────────────
  Assets before removal : 312
  Assets after removal  : 278
  Assets removed        : 34
  Uninstall mode        : uninstall-without-purge
```

## Uninstall modes

| Mode | API endpoint | Effect |
|------|-------------|--------|
| Default | `DELETE /api/public/assets/uninstall-without-purge` | Removes the AIR agent from the tenant and releases the license seat. Previously collected forensic evidence is **preserved**. |
| `--purge` | `DELETE /api/public/assets/purge-and-uninstall` | Removes the agent **and permanently deletes** all previously collected evidence for these endpoints. |

> **Warning:** `--purge` is irreversible. All DRONE findings, acquired evidence,
> and Investigation Hub data for the removed endpoints will be permanently
> deleted. Only use this mode after confirming that the evidence is no longer
> needed.

## Hostname matching

Matching is **case-insensitive** and uses the first DNS label only, so
`WORKSTATION-01.corp.acme.com` in the A-list will match `WORKSTATION-01` in
AIR (and vice versa). If multiple AIR assets share the same normalized
hostname, the first match is used.

## Troubleshooting

**`401 Unauthorized` or `403 Forbidden`**  
Check `BINALYZE_AIR_HOST` and `BINALYZE_API_TOKEN` in `.env`. The API token
must have asset management permissions (read assets + uninstall).

**`No organizations found`**  
The token does not have access to any organizations. Verify the token scope in
AIR under **Integrations > API Tokens**.

**`Cannot detect hostname column in A-list CSV`**  
The CSV does not have a recognized hostname header. Use `--hostname-column` to
specify the correct column name.

**`No matching assets found`**  
All hostnames in the A-list are absent from the AIR inventory. They may
already have been removed, or the hostnames in the A-list do not match the
`name` field used by AIR (e.g. IP address vs hostname). Use `--dry-run` first
to inspect the comparison before committing.

**Asset count does not decrease immediately after removal**  
AIR may take a few seconds to reflect the updated count. The script waits
3 seconds and then re-fetches the count. If the count still appears wrong,
refresh the AIR console and check the asset list directly.
