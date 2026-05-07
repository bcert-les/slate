# api/ — Binalyze AIR single-endpoint examples

Each script in this directory documents and invokes **one** HTTP route. They are
functional CLI examples; import shared helpers from `lib/` rather than each other.

Run all scripts from the repository root, e.g.:

```bash
python api/list_organizations.py
```

## Script index

| Script | Method | Route |
|--------|--------|-------|
| `list_organizations.py` | GET | `/api/public/organizations` |
| `get_organization.py` | GET | `/api/public/organizations/{id}` |
| `list_cases.py` | GET | `/api/public/cases` |
| `get_case.py` | GET | `/api/public/cases/{id}` |
| `post_case.py` | POST | `/api/public/cases` |
| `list_case_tasks.py` | GET | `/api/public/cases/{id}/tasks` |
| `list_assets.py` | GET | `/api/public/assets` |
| `export_assets.py` | GET | `/api/public/assets/export` |
| `post_assets_filter.py` | POST | `/api/public/assets/filter` |
| `list_asset_tasks.py` | GET | `/api/public/assets/{id}/tasks` |
| `list_acquisition_profiles.py` | GET | `/api/public/acquisitions/profiles` |
| `post_acquisitions_acquire.py` | POST | `/api/public/acquisitions/acquire` |
| `get_task.py` | GET | `/api/public/tasks/{id}` |
| `post_isolation_task.py` | POST | `/api/public/assets/tasks/isolation` |

## Typical discovery sequence

```bash
# 1. Find your org ID
python api/list_organizations.py

# 2. List open cases
python api/list_cases.py <org_id>

# 3. List endpoints
python api/list_assets.py <org_id>

# 4. Create a case
python api/post_case.py <org_id> --name "My investigation"

# 5. Check available acquisition profiles
python api/list_acquisition_profiles.py <org_id>

# 6. Assign acquisition (dry-run first)
python api/post_acquisitions_acquire.py <org_id> <case_id> <profile_id> <endpoint_id> --dry-run

# 7. Poll for completion
python api/get_task.py <task_id> --poll
```
