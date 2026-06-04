# Workflow: InterACT DNS isolation (hosts file)

PowerShell script for **manual deployment via Binalyze InterACT** on Windows endpoints.
It modifies the local `hosts` file to pin the AIR console hostname and sinkhole DNS
bypass domains, helping keep **responder agent ↔ console** connectivity while limiting
name-based egress during an isolation event.

This is a **supplement** to Binalyze network isolation (`POST /api/public/assets/tasks/isolation`), not a replacement. Hosts-file controls do not block raw IP traffic.

## Files

| File | Purpose |
|------|---------|
| `dns_isolation_hosts.ps1` | Enable / disable / status for the managed hosts block |
| `default_blocklist.txt` | Default sinkhole list (DoH providers, resolver hostnames) |

## Requirements

- Windows endpoint with Binalyze AIR responder installed
- **Administrator** privileges (InterACT remote shell runs elevated)
- Network path to resolve the console IP at enable time (or supply `-ConsoleIp`)

## Deploy via InterACT

### Option A — InterACT Library (recommended)

1. In the AIR console, open **InterACT → Library**.
2. Upload `dns_isolation_hosts.ps1` and `default_blocklist.txt` together (same folder path on the endpoint if possible).
3. Open an **InterACT remote shell** on the target endpoint.
4. Run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Path\From\Library\dns_isolation_hosts.ps1" -Action Enable -Force
```

If the console hostname cannot be read from agent `config.yml`, pass it explicitly:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Path\From\Library\dns_isolation_hosts.ps1" `
  -Action Enable -ConsoleHostname your-tenant.binalyze.com -Force
```

When the endpoint is already network-isolated and public DNS is unreliable, pin a known console IP:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Path\From\Library\dns_isolation_hosts.ps1" `
  -Action Enable -ConsoleHostname your-tenant.binalyze.com -ConsoleIp 203.0.113.10 -Force
```

### Option B — Paste into InterACT execute

For a one-off without library upload, paste a single enable command after copying the script to a temp path, or inline the enable call once the file exists on disk:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "& {
  Set-Content -Path '$env:TEMP\dns_isolation_hosts.ps1' -Value (Invoke-RestMethod 'https://internal-host/path/dns_isolation_hosts.ps1')
  & '$env:TEMP\dns_isolation_hosts.ps1' -Action Enable -ConsoleHostname your-tenant.binalyze.com -Force
}"
```

Replace the `Invoke-RestMethod` source with your internal script distribution URL, or upload via InterACT file transfer first.

### Option C — Command snippet

Save the enable one-liner (with your tenant hostname) as an **InterACT command snippet** for repeat use during incidents.

## Usage

Run from an elevated PowerShell session on the endpoint (or via InterACT):

```powershell
# Apply isolation block (reads console hostname from agent config.yml when omitted)
.\dns_isolation_hosts.ps1 -Action Enable

# Non-interactive (for InterACT)
.\dns_isolation_hosts.ps1 -Action Enable -ConsoleHostname your-tenant.binalyze.com -Force

# Include relay / evidence repository hostnames as allowed (pinned, not sinkholed)
.\dns_isolation_hosts.ps1 -Action Enable -AllowedHostnames relay.example.com,repo.example.com -Force

# Custom sinkhole list instead of/in addition to default
.\dns_isolation_hosts.ps1 -Action Enable -BlockListPath C:\Temp\extra_blocklist.txt -Force

# Console pinning only (no default sinkhole list)
.\dns_isolation_hosts.ps1 -Action Enable -SkipDefaultBlockList -Force

# Check current state
.\dns_isolation_hosts.ps1 -Action Status

# Revert
.\dns_isolation_hosts.ps1 -Action Disable -Force
```

## What the script does

1. **Detects** the AIR console hostname from `-ConsoleHostname` or responder `config.yml`
   (`C:\Program Files (x86)\binalyze\agent\config.yml`).
2. **Resolves** (or accepts) the console IPv4 and adds a **pinned allow** entry.
3. **Sinkholes** hostnames from `default_blocklist.txt` (and optional custom list) to `0.0.0.0`.
4. Wraps all changes between `# BEGIN BINALYZE-DNS-ISOLATION` / `# END BINALYZE-DNS-ISOLATION` markers.
5. **Backs up** the pre-change hosts file to `hosts.pre-binalyze-dns-isolation.bak` beside the script.
6. **Flushes** the local DNS cache (`ipconfig /flushdns`).

## Operational notes

- Verify agent check-in in the AIR console after enabling.
- IP-based egress is **not** blocked by this script; pair with Binalyze network isolation when full containment is required.
- Customize `default_blocklist.txt` before upload if your environment needs additional sinkhole domains.
- To unisolate at the API layer after remediation: `python api/post_isolation_task.py <org_id> <endpoint_id> --disable`

## Related

- [`api/post_isolation_task.py`](../../api/post_isolation_task.py) — Binalyze network isolation API
- [`workflows/isolation_xsoar/`](../isolation_xsoar/) — Case + XSOAR isolation workflow
