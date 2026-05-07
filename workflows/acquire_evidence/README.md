# Workflow: Acquire Evidence

Interactive CLI that replicates the full Binalyze AIR console acquisition flow:

1. Validate the organization
2. Find the endpoint (by hostname or asset ID)
3. Select an acquisition profile (interactive or by name/ID)
4. Create or reuse a case
5. POST to `/api/public/acquisitions/acquire`
6. Optionally poll until the task completes

## Requirements

- Python 3.9+
- Dependencies: `pip install -r requirements.txt`
- `.env` with `BINALYZE_AIR_HOST` and `BINALYZE_API_TOKEN`

## Usage

Run from the repository root:

```bash
# Interactive profile selection
python workflows/acquire_evidence/acquire_evidence.py <org_id> WORKSTATION-01

# Fully automated with polling
python workflows/acquire_evidence/acquire_evidence.py <org_id> WORKSTATION-01 \
  --profile-name "Quick triage" --poll

# Attach to an existing case
python workflows/acquire_evidence/acquire_evidence.py <org_id> WORKSTATION-01 \
  --case-id C-2026-00001

# Preview the acquire call without sending it
python workflows/acquire_evidence/acquire_evidence.py <org_id> WORKSTATION-01 \
  --profile-id abc123 --dry-run
```

## Options

| Flag | Description |
|------|-------------|
| `--case-id ID` | Reuse an existing open case |
| `--case-name NAME` | Create a new case with this name |
| `--profile-id ID` | Use this acquisition profile ID |
| `--profile-name NAME` | Find profile by name (case-insensitive) |
| `--poll` | Poll for task completion after assignment |
| `--poll-interval SECS` | Seconds between polls (default: 10) |
| `--dry-run` | Print the request body only; do not POST |
| `--case-visibility V` | `public-to-organization` \| `private-to-users` |

> **Note:** The POST `/api/public/acquisitions/acquire` request body is inferred
> from SDK patterns. The script prints the full request and response for debugging.
> Use `--dry-run` to inspect the body before sending.
