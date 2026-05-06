# Binalyze + XSOAR isolation workflow

Interactive CLI: [`scripts/api_scripts/workflow_isolation.py`](../scripts/api_scripts/workflow_isolation.py).

## What it does

1. Validates the Binalyze organization and resolves each endpoint (hostname or asset `_id`) with **no ambiguous matches** (non-interactive resolution).
2. **Blocks the entire run** if any resolved asset has a null/blank `name` (hostname).
3. Optionally matches **server-class hostnames** by regex; if any match, the operator must type the configured confirmation phrase.
4. Splits work into batches of **at most 5** endpoints (configurable); prompts between batches.
5. Creates or attaches a **Binalyze case** (first batch only; same case for later batches).
6. Creates a **Cortex XSOAR** incident (REST or webhook) with case task summary and structured `rawJSON` for automation.
7. Assigns **network isolation** on each endpoint via `POST /api/public/assets/tasks/isolation`.
8. Optionally **polls** asset tasks until the latest isolation-like task reaches a terminal status.
9. Prompts the operator to **review / unisolate** (no automatic unisolate in v1).

## Binalyze AIR: isolation request schema

The script sends JSON shaped as:

```json
{
  "organizationId": "<org_id>",
  "endpointIds": ["<asset_id>"],
  "isolation": "enable",
  "caseId": "<optional case id>"
}
```

To **release isolation**, the same path is used with `"isolation": "disable"` (aligned with the Cortex XSOAR Binalyze AIR integration parameters). If your tenant expects different property names, adjust [`lib/binalyze_isolation.py`](../lib/binalyze_isolation.py) `build_isolation_request_body()` and capture the HTTP response from a failed call for debugging.

## Environment variables

### Binalyze (existing)

| Variable | Required |
|----------|----------|
| `BINALYZE_AIR_HOST` | Yes |
| `BINALYZE_API_TOKEN` | Yes |

### Policy file

| Variable | Description |
|----------|-------------|
| `WORKFLOW_POLICY_PATH` | Path to JSON policy (default: `config/workflow_isolation.json` if that file exists; otherwise built-in defaults). |

Copy [`config/workflow_isolation.example.json`](../config/workflow_isolation.example.json) to `config/workflow_isolation.json` and edit.

### Cortex XSOAR

| Variable | Description |
|----------|-------------|
| `XSOAR_MODE` | `rest` (default) or `webhook` |
| `XSOAR_BASE_URL` | Server base URL, e.g. `https://xsoar.example.com` (no trailing slash) — **rest** mode |
| `XSOAR_API_KEY` | Server API key — **rest** mode |
| `XSOAR_INCIDENT_PATH` | Optional; default `/v1/incident` |
| `XSOAR_AUTH_HEADER` | Optional; default `Authorization` |
| `XSOAR_API_KEY_ID` | Optional; some setups use `x-xdr-auth-id` together with the key |
| `XSOAR_WEBHOOK_URL` | Required for **webhook** mode |
| `XSOAR_WEBHOOK_HEADERS_JSON` | Optional JSON object merged into webhook POST headers |

**Custom fields:** Put static field templates in the policy JSON under `xsoar_custom_fields`. XSOAR often expects nested objects per field (for example `{"myfield": {"simple": "value"}}`). Validate against your incident type in the UI.

## Usage

From the repository root (after `pip install -r requirements.txt`):

```bash
python scripts/api_scripts/workflow_isolation.py <org_id> HOST1 HOST2 \
  --audit-log output/isolation_audit.jsonl
```

Useful flags:

| Flag | Meaning |
|------|---------|
| `--case-id` | Attach to an existing case |
| `--case-name` | Create a new case with this name (when not using `--case-id`) |
| `--policy` | Path to policy JSON |
| `--dry-run` | Skip POSTs to isolation and XSOAR (GETs still run for resolution unless combined with care) |
| `--skip-xsoar` | Skip incident creation |
| `--no-poll` | Do not poll asset tasks after isolation |
| `--audit-log` | Append JSON Lines audit events |
| `--non-interactive` | Fail instead of prompting (for CI smoke tests only) |

## Idempotency

Re-running the workflow may create **another** XSOAR incident and **another** isolation task per endpoint. There is no built-in deduplication. Add operational checks (existing open incidents, current isolation state) before re-run if needed.

## Dependencies

[`lib/xsoar_adapter.py`](../lib/xsoar_adapter.py) uses `requests` (already in [`requirements.txt`](../requirements.txt)). The official `demisto-client` package is optional; this repo uses direct HTTP for fewer moving parts.
