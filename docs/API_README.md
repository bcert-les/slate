# Binalyze AIR API Reference

Complete API endpoint reference extracted from the official `@binalyze/air-sdk@5.13.2` (published March 2026).

All endpoints use **Bearer token authentication** via the `Authorization: Bearer {API_TOKEN}` header.
Base URL pattern: `https://{your-air-instance}/api/public/...`

---

## Table of Contents

- [Authentication](#authentication)
- [Organizations](#organizations)
- [Cases](#cases)
- [Tasks](#tasks)
- [Investigation Hub (Findings)](#investigation-hub-findings)
- [Assets / Endpoints](#assets--endpoints)
- [Evidence](#evidence)
- [Acquisitions](#acquisitions)
- [Triage](#triage)
- [Parameters / Lookups](#parameters--lookups)
- [User Management](#user-management)
- [API Tokens](#api-tokens)
- [Settings](#settings)
- [Workflows](#workflows)

---

## Authentication

Generate an API token from **Integrations > API Tokens** in the AIR console.

All requests require:
```
Authorization: Bearer {API_TOKEN}
Accept: application/json
Content-Type: application/json
```

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/auth/check` | Check current auth status |
| `POST` | `/api/public/auth/login` | Login |
| `GET` | `/api/public/auth/session-history` | Get user session history |
| `PUT` | `/api/public/auth/profile` | Update profile |
| `POST` | `/api/public/auth/refresh-personal-access-token` | Regenerate PAT token |
| `POST` | `/api/public/auth/one-click-login` | Generate one-click login URL |
| `POST` | `/api/public/auth/one-click-login/renew` | Renew one-click login URL |

---

## Organizations

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/organizations` | List all organizations (paginated) |
| `POST` | `/api/public/organizations` | Create organization |
| `GET` | `/api/public/organizations/{id}` | Get organization by ID |
| `PUT` | `/api/public/organizations/{id}` | Update organization |
| `DELETE` | `/api/public/organizations/{id}` | Delete organization |
| `GET` | `/api/public/organizations/check-name` | Check if org name exists |
| `GET` | `/api/public/organizations/{id}/users` | Get users in organization |
| `POST` | `/api/public/organizations/{id}/users` | Assign users to organization |
| `DELETE` | `/api/public/organizations/{id}/users` | Remove users from organization |
| `POST` | `/api/public/organizations/{id}/groups` | Assign groups to organization |
| `DELETE` | `/api/public/organizations/{id}/groups` | Remove groups from organization |
| `PUT` | `/api/public/organizations/{id}/deployment-token` | Update deployment token |
| `PUT` | `/api/public/organizations/{id}/shareable-deployment` | Update shareable deployment status |
| `GET` | `/api/public/organizations/token/{token}` | Get org ID by deployment token |
| `POST` | `/api/public/organizations/{id}/tags` | Add tags to organization |
| `DELETE` | `/api/public/organizations/{id}/tags` | Remove tags from organization |

---

## Cases

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/cases` | List cases (paginated, filterable) |
| `POST` | `/api/public/cases` | Create a new case |
| `GET` | `/api/public/cases/{id}` | Get case details |
| `PUT` | `/api/public/cases/{id}` | Update case |
| `POST` | `/api/public/cases/{id}/close` | Close a case |
| `POST` | `/api/public/cases/{id}/reopen` | Reopen a case |
| `POST` | `/api/public/cases/{id}/archive` | Archive a case |
| `PUT` | `/api/public/cases/{id}/owner` | Change case owner |
| `GET` | `/api/public/cases/{id}/tasks` | Get tasks for a case |
| `GET` | `/api/public/cases/{id}/endpoints` | Get endpoints in a case |
| `GET` | `/api/public/cases/{id}/activities` | Get case activities |
| `GET` | `/api/public/cases/{id}/users` | Get users assigned to case |
| `GET` | `/api/public/cases/{id}/task-assignments` | Filter task assignments for case |
| `POST` | `/api/public/cases/{id}/task-assignments/import` | Import task assignment to case |
| `DELETE` | `/api/public/cases/{id}/endpoints` | Remove endpoints from case |
| `DELETE` | `/api/public/cases/{id}/task-assignments/{assignmentId}` | Remove task assignment |
| `GET` | `/api/public/cases/check-name` | Check if case name exists |
| `POST` | `/api/public/cases/close` | Bulk close cases by filter |
| `POST` | `/api/public/cases/archive` | Bulk archive cases by filter |
| `GET` | `/api/public/cases/export` | Export cases |
| `GET` | `/api/public/cases/{id}/export/endpoints` | Export case endpoints |
| `GET` | `/api/public/cases/{id}/export/notes` | Export case notes |
| `GET` | `/api/public/cases/{id}/export/activities` | Export case activities |

### Case Notes

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/public/cases/{id}/notes` | Add note to case |
| `PUT` | `/api/public/cases/{id}/notes/{noteId}` | Edit case note |
| `DELETE` | `/api/public/cases/{id}/notes/{noteId}` | Delete case note |

### Case Categories

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/case-categories` | List case categories |
| `POST` | `/api/public/case-categories` | Create category |
| `DELETE` | `/api/public/case-categories/{id}` | Delete category |

### Case Tags

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/case-tags` | List case tags |
| `POST` | `/api/public/case-tags` | Create case tag |
| `DELETE` | `/api/public/case-tags/{id}` | Delete case tag |

---

## Tasks

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/tasks` | List all tasks (paginated) |
| `GET` | `/api/public/tasks/{id}` | Get specific task details |
| `GET` | `/api/public/tasks/{id}/data` | **Download task data/results** |
| `GET` | `/api/public/tasks/{id}/assignments` | Get task assignments |
| `GET` | `/api/public/tasks/display-types` | Get all possible task display types |
| `PATCH` | `/api/public/tasks/{id}/rename` | Rename a task |
| `POST` | `/api/public/tasks/{id}/cancel` | Cancel a task |
| `DELETE` | `/api/public/tasks/{id}` | Delete a task |
| `POST` | `/api/public/tasks/cancel-by-filter` | Bulk cancel tasks |
| `DELETE` | `/api/public/tasks/delete-by-filter` | Bulk delete tasks |
| `DELETE` | `/api/public/tasks/delete-and-purge-by-filter` | Bulk delete and purge tasks |

### Task Assignments

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/tasks/assignments/{id}` | Get task assignment |
| `POST` | `/api/public/tasks/assignments/{id}/cancel` | Cancel task assignment |
| `DELETE` | `/api/public/tasks/assignments/{id}` | Delete task assignment |
| `POST` | `/api/public/tasks/assignments/{id}/report` | Generate task assignment report |

### Off-Network Tasks

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/public/tasks/off-network` | Create off-network task |
| `POST` | `/api/public/tasks/off-network/assign` | Import off-network collection |
| `GET` | `/api/public/tasks/off-network/download` | Download off-network task |
| `POST` | `/api/public/tasks/off-network/generate-zip-password` | Generate ZIP password |

### Portable Disk Image

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/tasks/portable-disk-image/download` | Download portable disk image |
| `GET` | `/api/public/tasks/portable-disk-image/{taskId}/decryption-key` | Get decryption key |

---

## Investigation Hub (Findings)

These are the endpoints for accessing **DRONE findings** -- the actual forensic analysis results.

The workflow to retrieve findings:
1. Get a case via `GET /api/public/cases/{id}`
2. Extract `metadata.investigationId` from the response
3. Use the `investigationId` with the endpoints below

### Findings

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/investigation-hub/investigations/{investigationId}/findings/data-structure` | Get findings schema/column structure |
| `POST` | `/api/public/investigation-hub/investigations/{investigationId}/findings/filter` | **Filter and retrieve findings** |
| `POST` | `/api/public/investigation-hub/investigations/{investigationId}/findings/summary` | Get findings summary (counts by severity) |
| `POST` | `/api/public/investigation-hub/investigations/{investigationId}/findings/export` | Create export request for findings |
| `GET` | `/api/public/investigation-hub/investigations/{investigationId}/findings/export` | Download exported findings |

#### Findings Filter Request Body

```json
{
  "globalFilter": {
    "assignmentIds": ["task-assignment-id-1", "task-assignment-id-2"],
    "findingTypes": ["dangerous", "suspicious", "relevant", "matched", "rare"],
    "flagIds": [],
    "mitreTechniqueIds": [],
    "mitreTacticIds": [],
    "dateTimeRange": null
  },
  "filter": [
    { "column": "section", "operator": "!=", "value": "__never__" }
  ],
  "onlyExcludedFindings": false,
  "skip": 0,
  "take": 50,
  "sort": null
}
```

### Investigation Assets

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/investigation-hub/investigations/{investigationId}/assets` | Get assets/endpoints for investigation (returns `assignmentIds`) |
| `GET` | `/api/public/investigation-hub/investigations/{investigationId}/summary` | Get investigation summary |
| `GET` | `/api/public/investigation-hub/investigations/{investigationId}` | Get investigation by ID |
| `PUT` | `/api/public/investigation-hub/investigations/{investigationId}` | Update investigation |

### Evidence Data

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/investigation-hub/investigations/{investigationId}/evidence/data-structure` | Get evidence data structure |
| `POST` | `/api/public/investigation-hub/investigations/{investigationId}/evidence/data` | Get evidence data rows |
| `GET` | `/api/public/investigation-hub/investigations/{investigationId}/evidence/relation-data` | Get evidence relation data |
| `GET` | `/api/public/investigation-hub/investigations/{investigationId}/evidence/counts` | Get evidence counts |
| `GET` | `/api/public/investigation-hub/investigations/{investigationId}/sections` | Get sections by endpoints |
| `POST` | `/api/public/investigation-hub/investigations/{investigationId}/evidence/export` | Create evidence export request |
| `GET` | `/api/public/investigation-hub/investigations/{investigationId}/evidence/export` | Download exported evidence |

### MITRE ATT&CK

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/public/investigation-hub/investigations/{investigationId}/mitre/matches` | Get MITRE technique matches |

### Flags & Bookmarks

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/investigation-hub/investigations/{investigationId}/flags` | List flags |
| `POST` | `/api/public/investigation-hub/investigations/{investigationId}/flags` | Create flag |
| `PUT` | `/api/public/investigation-hub/investigations/{investigationId}/flags/{flagId}` | Update flag |
| `DELETE` | `/api/public/investigation-hub/investigations/{investigationId}/flags/{flagId}` | Delete flag |
| `POST` | `/api/public/investigation-hub/investigations/{investigationId}/evidence/flag` | Flag evidence |
| `DELETE` | `/api/public/investigation-hub/investigations/{investigationId}/evidence/flag` | Remove evidence flag |
| `POST` | `/api/public/investigation-hub/investigations/{investigationId}/flags/export` | Create flags export request |
| `GET` | `/api/public/investigation-hub/investigations/{investigationId}/flags/export` | Download exported flags |

### Comments

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/investigation-hub/investigations/{investigationId}/comments` | List comments |
| `POST` | `/api/public/investigation-hub/investigations/{investigationId}/comments` | Add comment |
| `DELETE` | `/api/public/investigation-hub/investigations/{investigationId}/comments/{commentId}` | Delete comment |

### Finding Exclusion Rules

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/investigation-hub/investigations/{investigationId}/exclusions` | List exclusion rules |
| `POST` | `/api/public/investigation-hub/investigations/{investigationId}/exclusions` | Add exclusion rule |
| `DELETE` | `/api/public/investigation-hub/investigations/{investigationId}/exclusions/{ruleId}` | Delete exclusion rule |
| `DELETE` | `/api/public/investigation-hub/investigations/{investigationId}/exclusions/individual/{ruleId}` | Delete individual exclusion |
| `GET` | `/api/public/investigation-hub/investigations/{investigationId}/exclusions/reasons` | Get exclusion reasons |
| `GET` | `/api/public/investigation-hub/investigations/{investigationId}/exclusions/matches` | Get exclusion rule matches |
| `POST` | `/api/public/investigation-hub/investigations/{investigationId}/exclusions/apply/all-assets` | Apply exclusion for all assets |
| `POST` | `/api/public/investigation-hub/investigations/{investigationId}/exclusions/apply/organization` | Apply exclusion for organization |

### Findings Marking

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/public/investigation-hub/investigations/{investigationId}/evidence/mark-as-finding` | Mark evidence as finding |
| `PUT` | `/api/public/investigation-hub/investigations/{investigationId}/findings/{findingId}` | Update finding |

### Timeline

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/public/investigation-hub/investigations/{investigationId}/timeline/filter` | Filter timeline events |
| `GET` | `/api/public/investigation-hub/investigations/{investigationId}/timeline/structure` | Get timeline structure |
| `GET` | `/api/public/investigation-hub/investigations/{investigationId}/timeline/total-count` | Get total timeline count |
| `POST` | `/api/public/investigation-hub/investigations/{investigationId}/timeline/count` | Get count by advanced filter |
| `POST` | `/api/public/investigation-hub/investigations/{investigationId}/timeline/export` | Create timeline export request |
| `GET` | `/api/public/investigation-hub/investigations/{investigationId}/timeline/export` | Download exported timeline |

### Global Search

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/public/investigation-hub/investigations/{investigationId}/global-search` | Global search within investigation |

### Reports

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/investigation-hub/investigations/{investigationId}/reports` | List reports |
| `GET` | `/api/public/investigation-hub/investigations/{investigationId}/reports/export` | Export reports |
| `POST` | `/api/public/investigation-hub/investigations/{investigationId}/reports` | Create report |
| `GET` | `/api/public/investigation-hub/investigations/{investigationId}/reports/{reportId}` | Get report |
| `PUT` | `/api/public/investigation-hub/investigations/{investigationId}/reports/{reportId}` | Update report |
| `DELETE` | `/api/public/investigation-hub/investigations/{investigationId}/reports/{reportId}` | Delete report |
| `POST` | `/api/public/investigation-hub/investigations/{investigationId}/reports/{reportId}/generate-pdf` | Generate PDF report |
| `GET` | `/api/public/investigation-hub/investigations/{investigationId}/reports/{reportId}/download-pdf` | Download PDF report |

### Activities

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/investigation-hub/investigations/{investigationId}/activities` | List investigation activities |
| `POST` | `/api/public/investigation-hub/investigations/{investigationId}/activities/mark-as-read` | Mark activities as read |

### Advanced Filters

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/investigation-hub/investigations/{investigationId}/advanced-filters` | List saved filters |
| `POST` | `/api/public/investigation-hub/investigations/{investigationId}/advanced-filters` | Create filter |
| `PUT` | `/api/public/investigation-hub/investigations/{investigationId}/advanced-filters/{filterId}` | Update filter |
| `DELETE` | `/api/public/investigation-hub/investigations/{investigationId}/advanced-filters/{filterId}` | Delete filter |

### Data Import

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/public/investigation-hub/investigations/{investigationId}/evidence` | Create investigation evidence |
| `PUT` | `/api/public/investigation-hub/investigations/{investigationId}/evidence/{evidenceId}` | Update investigation evidence |
| `POST` | `/api/public/investigation-hub/investigations/{investigationId}/evidence/import` | Import data to investigation |
| `POST` | `/api/public/investigation-hub/investigations/{investigationId}/import/pst` | Import PST evidence |
| `POST` | `/api/public/investigation-hub/investigations/{investigationId}/import/tornado` | Import Tornado evidence |
| `GET` | `/api/public/investigation-hub/investigations/{investigationId}/imports` | Get all imports |
| `DELETE` | `/api/public/investigation-hub/investigations/{investigationId}/imports/{importId}` | Delete import |
| `GET` | `/api/public/investigation-hub/investigations/{investigationId}/imports/{importId}/errors` | Get import errors |
| `POST` | `/api/public/investigation-hub/investigations/{investigationId}/imports/{importId}/retry` | Retry import |
| `GET` | `/api/public/investigation-hub/investigations/{investigationId}/import-progress` | Get data import progress |
| `GET` | `/api/public/investigation-hub/investigations/{investigationId}/task-assignment-import-progress` | Get task assignment import progress |
| `GET` | `/api/public/investigation-hub/investigations/{investigationId}/imported-evidence-task-assignment` | Get imported evidence task assignment |

### Task Execution Logs

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/investigation-hub/investigations/{investigationId}/task-execution-logs` | Get task execution logs |
| `POST` | `/api/public/investigation-hub/investigations/{investigationId}/task-execution-logs/filter` | Filter task execution logs |

### Data Usage

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/investigation-hub/data-usage/actual` | Get actual data usages |
| `POST` | `/api/public/investigation-hub/data-usage/actual/export` | Create actual data usage export |
| `GET` | `/api/public/investigation-hub/data-usage/actual/export` | Download actual data usage export |
| `GET` | `/api/public/investigation-hub/data-usage/actual/statistics` | Get actual data usage statistics |
| `GET` | `/api/public/investigation-hub/data-usage/historical/statistics` | Get historical data usage statistics |

---

## Assets / Endpoints

Both `/api/public/endpoints` and `/api/public/assets` paths are supported (aliases).

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/assets` | List assets (paginated) |
| `POST` | `/api/public/assets/filter` | Filter assets (POST body) |
| `GET` | `/api/public/assets/{id}` | Get asset details |
| `GET` | `/api/public/assets/{id}/tasks` | Get tasks for asset |
| `GET` | `/api/public/assets/{id}/cases` | Get cases for asset |
| `GET` | `/api/public/assets/search` | Search assets |
| `GET` | `/api/public/assets/stats` | Get asset statistics |
| `GET` | `/api/public/assets/with-task-count` | Get assets with task counts |
| `GET` | `/api/public/assets/export` | Export assets |
| `GET` | `/api/public/assets/{id}/cases/export` | Export cases for asset |
| `PATCH` | `/api/public/assets/{id}/tag` | Update asset tags |
| `PATCH` | `/api/public/assets/{id}/label` | Update asset label |
| `POST` | `/api/public/assets/tags` | Add tags to assets by filter |
| `DELETE` | `/api/public/assets/tags` | Remove tags from assets |
| `GET` | `/api/public/assets/disks` | Filter disks |
| `GET` | `/api/public/assets/device-name-conflicts` | Get device name conflicts |
| `GET` | `/api/public/assets/{id}/cloud-asset-details` | Get cloud asset details |
| `POST` | `/api/public/assets/deploy` | Deploy cloud assets |
| `PATCH` | `/api/public/assets/connection-route` | Update connection route |
| `POST` | `/api/public/assets/{endpointId}/maintenance-mode` | Set maintenance mode |
| `POST` | `/api/public/assets/sync` | Sync LDAP |
| `POST` | `/api/public/assets/import/ppc` | Import PPC as endpoint |
| `POST` | `/api/public/assets/{id}/import/ppc` | Import PPC to existing endpoint |
| `GET` | `/api/public/assets/{endpointId}/logs/{taskId}` | Download log file |
| `GET` | `/api/public/assets/{vendorDeviceId}/comparable-tasks` | Get comparable tasks |

### Asset Tasks

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/public/assets/tasks/reboot` | Assign reboot task |
| `POST` | `/api/public/assets/tasks/shutdown` | Assign shutdown task |
| `POST` | `/api/public/assets/tasks/isolation` | Assign isolation task |
| `POST` | `/api/public/assets/tasks/retrieve-logs` | Assign log retrieval task |
| `POST` | `/api/public/assets/tasks/version-update` | Assign version update task |
| `POST` | `/api/public/assets/tasks/purge-local-task-data` | Purge local task data |
| `GET` | `/api/public/assets/tasks/{taskAssignmentId}/retry-upload` | Retry task upload |
| `DELETE` | `/api/public/assets/purge-and-uninstall` | Uninstall and purge |
| `DELETE` | `/api/public/assets/uninstall-without-purge` | Uninstall without purge |
| `DELETE` | `/api/public/assets/purge-without-uninstall` | Purge without uninstall |
| `POST` | `/api/public/assets/update-exclusion` | Exclude from updates |

### Asset Tags & Groups

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/asset-tags` | List asset tags |
| `DELETE` | `/api/public/asset-tags/{organizationId}/all` | Delete all tags for org |
| `DELETE` | `/api/public/asset-tags/{organizationId}/{id}` | Delete specific tag |
| `GET` | `/api/public/asset-groups/root/{organizationId}` | Get root asset groups |
| `GET` | `/api/public/asset-groups/{id}` | Get child groups |

---

## Evidence

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/evidence/case-ppc-file` | Download Case.db / PPC file |
| `GET` | `/api/public/evidence/case-report` | Get case report |
| `GET` | `/api/public/evidence/case-report-file-info` | Get case report file info |
| `GET` | `/api/public/evidence/external/download` | Download external evidence |

### Evidence Repositories

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/evidence/repositories` | List evidence repositories |
| `GET` | `/api/public/evidence/repositories/{id}` | Get repository details |
| `DELETE` | `/api/public/evidence/repositories/{id}` | Delete repository |
| `POST` | `/api/public/evidence/repositories/{id}/validate` | Validate repository by ID |
| `POST` | `/api/public/evidence/repositories/smb` | Create SMB repository |
| `PUT` | `/api/public/evidence/repositories/smb` | Update SMB repository |
| `POST` | `/api/public/evidence/repositories/sftp` | Create SFTP repository |
| `PUT` | `/api/public/evidence/repositories/sftp` | Update SFTP repository |
| `POST` | `/api/public/evidence/repositories/ftps` | Create FTPS repository |
| `PUT` | `/api/public/evidence/repositories/ftps` | Update FTPS repository |
| `POST` | `/api/public/evidence/repositories/amazon-s3` | Create Amazon S3 repository |
| `PUT` | `/api/public/evidence/repositories/amazon-s3` | Update Amazon S3 repository |
| `POST` | `/api/public/evidence/repositories/azure-storage` | Create Azure Storage repository |
| `PUT` | `/api/public/evidence/repositories/azure-storage` | Update Azure Storage repository |
| `POST` | `/api/public/evidence/repositories/google-cloud-storage` | Create GCS repository |
| `PUT` | `/api/public/evidence/repositories/google-cloud-storage` | Update GCS repository |
| `POST` | `/api/public/evidence/repositories/validate/amazon-s3` | Validate S3 settings |
| `POST` | `/api/public/evidence/repositories/validate/azure-storage` | Validate Azure settings |
| `POST` | `/api/public/evidence/repositories/validate/ftps` | Validate FTPS settings |
| `POST` | `/api/public/evidence/repositories/validate/google-cloud-storage` | Validate GCS settings |

---

## Acquisitions

### Acquisition Profiles

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/acquisitions/profiles` | List acquisition profiles |
| `POST` | `/api/public/acquisitions/profiles` | Create profile |
| `GET` | `/api/public/acquisitions/profiles/{id}` | Get profile |
| `PUT` | `/api/public/acquisitions/profiles/{id}` | Update profile |
| `DELETE` | `/api/public/acquisitions/profiles/{id}` | Delete profile |
| `DELETE` | `/api/public/acquisitions/profiles/bulk` | Bulk delete profiles |

### Assign Evidence Tasks

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/public/acquisitions/assign-task` | Assign evidence acquisition task |
| `POST` | `/api/public/acquisitions/image` | Acquire disk image |
| `POST` | `/api/public/acquisitions/portable-disk-image` | Create portable disk image task |
| `POST` | `/api/public/acquisitions/off-network` | Create off-network acquisition task file |
| `PUT` | `/api/public/acquisitions/scheduled/evidence` | Update scheduled evidence acquisition |
| `PUT` | `/api/public/acquisitions/scheduled/image` | Update scheduled image acquisition |
| `POST` | `/api/public/acquisitions/validate-osqueries` | Validate osquery queries |

### Cloud Acquisition Profiles

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/cloud-acquisitions/profiles` | List cloud acquisition profiles |
| `POST` | `/api/public/cloud-acquisitions/profiles` | Create profile |
| `GET` | `/api/public/cloud-acquisitions/profiles/{id}` | Get profile |
| `PUT` | `/api/public/cloud-acquisitions/profiles/{id}` | Update profile |
| `DELETE` | `/api/public/cloud-acquisitions/profiles/{id}` | Delete profile |

### Image Evidence Acquisition Profiles

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/image-evidence-acquisitions/profiles` | List profiles |
| `POST` | `/api/public/image-evidence-acquisitions/profiles` | Create profile |
| `GET` | `/api/public/image-evidence-acquisitions/profiles/{id}` | Get profile |
| `PUT` | `/api/public/image-evidence-acquisitions/profiles/{id}` | Update profile |
| `DELETE` | `/api/public/image-evidence-acquisitions/profiles/{id}` | Delete profile |
| `DELETE` | `/api/public/image-evidence-acquisitions/profiles/bulk` | Bulk delete profiles |

---

## Triage

### Triage Rules

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/triage/rules` | List triage rules |
| `POST` | `/api/public/triage/rules` | Create triage rule |
| `GET` | `/api/public/triage/rules/{id}` | Get triage rule |
| `PUT` | `/api/public/triage/rules/{id}` | Update triage rule |
| `DELETE` | `/api/public/triage/rules/{id}` | Delete triage rule |
| `DELETE` | `/api/public/triage/rules/bulk` | Bulk delete triage rules |
| `POST` | `/api/public/triage/rules/validate` | Validate triage rule |
| `POST` | `/api/public/triage/assign-task` | Assign triage task |
| `PUT` | `/api/public/triage/scheduled` | Update scheduled triage task |
| `POST` | `/api/public/triage/off-network` | Create off-network triage task file |

### Triage Rule Tags

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/triage/rule-tags` | List triage rule tags |
| `POST` | `/api/public/triage/rule-tags` | Create tag |
| `DELETE` | `/api/public/triage/rule-tags/{id}` | Delete tag |

---

## Parameters / Lookups

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/params/drone/analyzers` | List DRONE analyzers |
| `GET` | `/api/public/params/acquisition/evidences` | List evidence types |
| `GET` | `/api/public/params/acquisition/event-log-record-types` | List event log record types |
| `GET` | `/api/public/params/acquisition/e-discovery-patterns` | List e-discovery patterns |
| `GET` | `/api/public/params/mitre-attack/tactics` | List MITRE ATT&CK tactics |

---

## User Management

### Users

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/user-management/users` | List users (paginated) |
| `POST` | `/api/public/user-management/users` | Create user |
| `GET` | `/api/public/user-management/users/{id}` | Get user |
| `PUT` | `/api/public/user-management/users/{id}` | Update user |
| `PATCH` | `/api/public/user-management/users/{id}` | Patch user |
| `DELETE` | `/api/public/user-management/users/{id}` | Delete user |
| `POST` | `/api/public/user-management/users/{id}/reset-tfa` | Reset TFA |
| `POST` | `/api/public/user-management/users/{id}/reset-password` | Reset password |
| `PUT` | `/api/public/user-management/users/change-password` | Change password |
| `POST` | `/api/public/user-management/users/api-user` | Create API user |

### Roles

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/user-management/roles` | List roles |
| `POST` | `/api/public/user-management/roles` | Create role |
| `GET` | `/api/public/user-management/roles/{id}` | Get role |
| `PUT` | `/api/public/user-management/roles/{id}` | Update role |
| `DELETE` | `/api/public/user-management/roles/{id}` | Delete role |
| `GET` | `/api/public/user-management/roles/privileges` | List privileges |

### User Groups

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/user-management/user-groups` | List user groups |
| `POST` | `/api/public/user-management/user-groups` | Create group |
| `GET` | `/api/public/user-management/user-groups/{id}` | Get group |
| `PUT` | `/api/public/user-management/user-groups/{id}` | Update group |
| `DELETE` | `/api/public/user-management/user-groups/{id}` | Delete group |

---

## API Tokens

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/api-tokens` | List API tokens |
| `POST` | `/api/public/api-tokens` | Create API token |
| `GET` | `/api/public/api-tokens/{id}` | Get token |
| `PUT` | `/api/public/api-tokens/{id}` | Update token |
| `DELETE` | `/api/public/api-tokens/{id}` | Delete token |
| `DELETE` | `/api/public/api-tokens/bulk` | Bulk delete tokens |

---

## Additional Endpoints

### InterACT (Remote Shell)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/public/interact/shell/assign-task` | Assign shell task |
| `POST` | `/api/public/interact/sessions/{sessionId}/execute` | Execute command |
| `POST` | `/api/public/interact/sessions/{sessionId}/execute-async` | Execute async command |
| `POST` | `/api/public/interact/sessions/{sessionId}/interrupt` | Interrupt command |
| `POST` | `/api/public/interact/sessions/{sessionId}/close` | Close session |
| `GET` | `/api/public/interact/sessions/{sessionId}/messages/{messageId}` | Get command message |
| `GET` | `/api/public/interact/commands` | List commands |
| `GET` | `/api/public/interact/download` | Download interact data |
| `GET` | `/api/public/interact/shell-report` | Get shell report |

### InterACT Command Snippets

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/interact/command-snippets` | List snippets |
| `POST` | `/api/public/interact/command-snippets` | Create snippet |
| `GET` | `/api/public/interact/command-snippets/{id}` | Get snippet |
| `PUT` | `/api/public/interact/command-snippets/{id}` | Update snippet |
| `DELETE` | `/api/public/interact/command-snippets/{id}` | Delete snippet |
| `DELETE` | `/api/public/interact/command-snippets/bulk` | Bulk delete snippets |

### InterACT Library

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/interact/library` | List library files |
| `POST` | `/api/public/interact/library/upload` | Upload file |
| `GET` | `/api/public/interact/library/download` | Download file |
| `DELETE` | `/api/public/interact/library/{id}` | Delete file |
| `DELETE` | `/api/public/interact/library/bulk` | Bulk delete files |
| `GET` | `/api/public/interact/library/check-exists` | Check file exists |

### Baseline Comparison

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/public/baseline/compare` | Compare baselines |
| `POST` | `/api/public/baseline/acquire` | Acquire baseline |
| `GET` | `/api/public/baseline/comparison-report` | Get comparison report |
| `GET` | `/api/public/baseline/comparison-report/{vendorDeviceId}` | Get report by vendor device ID |

### Audit Logs

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/audit-logs` | List audit logs |
| `GET` | `/api/public/audit-logs/export` | Export audit logs |

### Notifications

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/notifications` | List notifications |
| `DELETE` | `/api/public/notifications` | Delete all notifications |
| `POST` | `/api/public/notifications/mark-all-as-read` | Mark all as read |
| `POST` | `/api/public/notifications/{id}/mark-as-read` | Mark as read |

### Policies

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/policies` | List policies |
| `POST` | `/api/public/policies` | Create policy |
| `GET` | `/api/public/policies/{id}` | Get policy |
| `PUT` | `/api/public/policies/{id}` | Update policy |
| `DELETE` | `/api/public/policies/{id}` | Delete policy |
| `PUT` | `/api/public/policies/priorities` | Update policy priorities |
| `GET` | `/api/public/policies/check-name` | Check policy name exists |
| `GET` | `/api/public/policies/match-stats` | Get policy match statistics |

### Webhooks

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/webhooks` | List webhooks |
| `POST` | `/api/public/webhooks` | Create webhook |
| `GET` | `/api/public/webhooks/{id}` | Get webhook |
| `PUT` | `/api/public/webhooks/{id}` | Update webhook |
| `DELETE` | `/api/public/webhooks/{id}` | Delete webhook |
| `DELETE` | `/api/public/webhooks/bulk` | Bulk delete webhooks |

### Event Subscriptions

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/event-subscriptions` | List event subscriptions |
| `POST` | `/api/public/event-subscriptions` | Create subscription |
| `GET` | `/api/public/event-subscriptions/{id}` | Get subscription |
| `PUT` | `/api/public/event-subscriptions/{id}` | Update subscription |
| `DELETE` | `/api/public/event-subscriptions/{id}` | Delete subscription |
| `DELETE` | `/api/public/event-subscriptions/bulk` | Bulk delete subscriptions |
| `GET` | `/api/public/event-subscriptions/event-names` | List subscription event names |
| `POST` | `/api/public/event-subscriptions/{id}/test` | Test event subscription |

### Cloud Accounts

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/cloud-accounts` | List cloud accounts |
| `POST` | `/api/public/cloud-accounts` | Create cloud account |
| `GET` | `/api/public/cloud-accounts/{id}` | Get cloud account |
| `PUT` | `/api/public/cloud-accounts/{id}` | Update cloud account |
| `DELETE` | `/api/public/cloud-accounts/{id}` | Delete cloud account |
| `POST` | `/api/public/cloud-accounts/{id}/sync` | Sync cloud account |
| `POST` | `/api/public/cloud-accounts/sync-all` | Sync all cloud accounts |
| `GET` | `/api/public/cloud-accounts/export` | Export cloud accounts |
| `GET` | `/api/public/cloud-accounts/config` | Get cloud accounts config |

### Automation Hub

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/automation-hub` | List automations |
| `POST` | `/api/public/automation-hub` | Create automation |
| `GET` | `/api/public/automation-hub/{id}` | Get automation |
| `PUT` | `/api/public/automation-hub/{id}` | Update automation |
| `DELETE` | `/api/public/automation-hub/{id}` | Delete automation |
| `POST` | `/api/public/automation-hub/{id}/enable` | Enable automation |
| `POST` | `/api/public/automation-hub/{id}/disable` | Disable automation |
| `GET` | `/api/public/automation-hub/{id}/executions` | Get automation executions |
| `GET` | `/api/public/automation-hub/{id}/change-history` | Get change history |
| `GET` | `/api/public/automation-hub/execution-history` | Get execution history |
| `GET` | `/api/public/automation-hub/trigger-types` | Get trigger types |
| `GET` | `/api/public/automation-hub/action-types` | Get action types |
| `POST` | `/api/public/automation-hub/test-action` | Test action |
| `POST` | `/api/public/automation-hub/execute-integration` | Execute integration |
| `GET` | `/api/public/automation-hub/tags` | Get automation tags |

### Full Text Search (E-Discovery)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/full-text-search/profiles` | List profiles |
| `POST` | `/api/public/full-text-search/profiles` | Create profile |
| `GET` | `/api/public/full-text-search/profiles/{id}` | Get profile |
| `PUT` | `/api/public/full-text-search/profiles/{id}` | Update profile |
| `DELETE` | `/api/public/full-text-search/profiles/{id}` | Delete profile |
| `DELETE` | `/api/public/full-text-search/profiles/bulk` | Bulk delete profiles |
| `POST` | `/api/public/full-text-search/assign-task` | Assign search task |
| `PUT` | `/api/public/full-text-search/scheduled` | Update scheduled search |

### Auto Asset Tags

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/auto-asset-tags` | List auto asset tags |
| `POST` | `/api/public/auto-asset-tags` | Create auto asset tag |
| `GET` | `/api/public/auto-asset-tags/{id}` | Get auto asset tag |
| `PUT` | `/api/public/auto-asset-tags/{id}` | Update auto asset tag |
| `DELETE` | `/api/public/auto-asset-tags/{id}` | Delete auto asset tag |
| `DELETE` | `/api/public/auto-asset-tags/bulk` | Bulk delete |
| `POST` | `/api/public/auto-asset-tags/assign-task` | Assign auto tagging task |
| `PUT` | `/api/public/auto-asset-tags/scheduled` | Update scheduled tagging |

### Preset Filters

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/preset-filters` | List preset filters |
| `POST` | `/api/public/preset-filters` | Create preset filter |
| `PUT` | `/api/public/preset-filters/{id}` | Update preset filter |
| `DELETE` | `/api/public/preset-filters/{id}` | Delete preset filter |

### Recent Activities

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/recent-activities` | List recent activities |
| `POST` | `/api/public/recent-activities` | Create recent activity |

### Relay Servers

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/relay-servers/{id}` | Get relay server |
| `GET` | `/api/public/relay-servers` | List relay servers |
| `PATCH` | `/api/public/relay-servers/{id}/tags` | Update relay tags |
| `PATCH` | `/api/public/relay-servers/{id}/label` | Update relay label |
| `DELETE` | `/api/public/relay-servers/{id}` | Remove relay server |
| `PUT` | `/api/public/relay-servers/{id}/address` | Update relay address |
| `POST` | `/api/public/relay-servers/tasks/reboot` | Reboot relay |
| `POST` | `/api/public/relay-servers/tasks/retrieve-logs` | Retrieve relay logs |
| `POST` | `/api/public/relay-servers/tasks/version-update` | Update relay version |
| `PUT` | `/api/public/relay-pro/url` | Update Relay Pro URL |
| `DELETE` | `/api/public/relay-pro` | Remove Relay Pro |

### App Info

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/app/info` | Get AIR console basic info |

### Logs

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/logs` | Download logs directory |

### Multipart Upload

| Method | Path | Description |
|--------|------|-------------|
| `HEAD` | `/api/public/multipart-upload/initialize` | Initialize upload (HEAD) |
| `POST` | `/api/public/multipart-upload/initialize` | Initialize upload |
| `POST` | `/api/public/multipart-upload/finalize` | Finalize upload |
| `GET` | `/api/public/multipart-upload/is-ready` | Check if ready |
| `DELETE` | `/api/public/multipart-upload/parts` | Delete file parts |
| `POST` | `/api/public/multipart-upload/part` | Upload part |

### Backup

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/backup` | List backups |
| `POST` | `/api/public/backup` | Create backup |
| `POST` | `/api/public/backup/restore` | Restore backup |

### Processors

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/public/processors` | Get all processors |
| `GET` | `/api/public/processors/{assetType}` | Get processors by asset type |
| `GET` | `/api/public/processors/{assetType}/type` | Get processor type by asset |

---

## Pagination

List endpoints support pagination via query parameters:

| Parameter | Description |
|-----------|-------------|
| `pageNumber` | Page number (1-based) |
| `pageSize` | Items per page |
| `sortBy` | Field to sort by |
| `sortType` | `ASC` or `DESC` |

Response includes `_pagination` metadata with total counts and page information.

---

## Source

This reference was extracted from the official `@binalyze/air-sdk@5.13.2` TypeScript SDK (auto-generated from the OpenAPI spec), published March 2026 on npm.
