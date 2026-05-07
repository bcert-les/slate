# Workflow: Isolation + XSOAR

Interactive CLI that validates endpoints in Binalyze AIR, creates a case, opens a
Cortex XSOAR incident, assigns isolation tasks in batches, polls for completion, and
prompts the operator before server-class hosts or between batches.

## Requirements

- Python 3.9+
- Dependencies: `pip install -r requirements.txt`
- `.env` at the repository root:
  ```env
  BINALYZE_AIR_HOST=https://your-tenant.binalyze.com
  BINALYZE_API_TOKEN=api_your_token_here
  ```
- XSOAR connection configured via a policy JSON file. Set the env variable
  `WORKFLOW_POLICY_PATH` to point to your config file.

## Usage

Run from the repository root:

```bash
python workflows/isolation_xsoar/isolation_xsoar.py <org_id> HOST1 HOST2 ...

# Skip XSOAR for testing
python workflows/isolation_xsoar/isolation_xsoar.py <org_id> HOST1 --skip-xsoar

# Dry-run (reads only, no isolation or XSOAR mutations)
python workflows/isolation_xsoar/isolation_xsoar.py <org_id> HOST1 HOST2 --dry-run

# Non-interactive (CI/automation — fails if operator prompts would be needed)
python workflows/isolation_xsoar/isolation_xsoar.py <org_id> HOST1 --non-interactive --skip-xsoar
```

## Options

| Flag | Description |
|------|-------------|
| `--case-id ID` | Reuse an existing Binalyze case |
| `--case-name NAME` | Name for the new case |
| `--policy PATH` | Policy JSON (overrides `WORKFLOW_POLICY_PATH`) |
| `--dry-run` | Skip isolation and XSOAR writes; still reads from the API |
| `--skip-xsoar` | Omit the XSOAR incident step entirely |
| `--audit-log PATH` | Append JSONL audit events to this file |
| `--poll-interval SECS` | Seconds between isolation polls (default: 10) |
| `--poll-timeout SECS` | Max poll time per endpoint (default: 3600) |
| `--no-poll` | Skip polling after isolation assignment |
| `--non-interactive` | Abort instead of prompting (for automation) |

## Policy / config

Copy `config/workflow_isolation.example.json` and customize:

```json
{
  "max_batch_size": 5,
  "server_hostname_regex": "(?i)(srv|svr|dc|sql|prod)",
  "server_confirmation_phrase": "I confirm isolation",
  "xsoar_incident_type": "Binalyze AIR Isolation",
  "xsoar_severity": 3
}
```

Point to your config with `WORKFLOW_POLICY_PATH=/path/to/workflow_isolation.json` in `.env`.

## Unisolating endpoints

When remediation is complete, run:

```bash
python api/post_isolation_task.py <org_id> <endpoint_id> --disable
```

Or use the Binalyze AIR console: Assets → select endpoint → Network Isolation → Disable.
