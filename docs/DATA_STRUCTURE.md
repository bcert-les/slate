# Binalyze AIR Data Structure

Visual tree of how entities relate in the Binalyze AIR API, from tenant root down to individual evidence rows.

---

## Entity Hierarchy

```
AIR Tenant
│
├── Organizations
│   ├── _id
│   ├── name
│   ├── Users[]
│   ├── Groups[]
│   ├── Tags[]
│   ├── deploymentToken
│   │
│   ├── Assets (Endpoints)                    ← GET /assets, POST /assets/filter
│   │   ├── _id
│   │   ├── name                              ← hostname / device name
│   │   ├── os                                ← e.g. "Windows 10 Enterprise"
│   │   ├── platform                          ← windows | linux | macos
│   │   ├── ipAddress
│   │   ├── vendorDeviceId
│   │   ├── Tags[]
│   │   ├── Label
│   │   ├── Disks[]
│   │   └── Asset Tasks                       ← reboot, shutdown, isolation, etc.
│   │
│   ├── Cases                                 ← GET /cases  (filter[organizationIds] required)
│   │   ├── _id                               ← case ID (e.g. "C-2026-00001")
│   │   ├── name
│   │   ├── status                            ← open | closed | archived
│   │   ├── owner
│   │   ├── organizationId
│   │   ├── category { name }
│   │   ├── totalEndpoints
│   │   ├── createdAt
│   │   ├── metadata
│   │   │   ├── investigationId               ← links to Investigation Hub
│   │   │   └── diskUsageInBytes
│   │   │
│   │   ├── Endpoints[]                       ← GET /cases/{id}/endpoints
│   │   │   ├── _id
│   │   │   ├── name
│   │   │   ├── os
│   │   │   ├── platform
│   │   │   └── ipAddress
│   │   │
│   │   ├── Tasks[]                           ← GET /cases/{id}/tasks
│   │   │   ├── taskId
│   │   │   ├── name
│   │   │   ├── type                          ← "acquisition" | "triage" | ...
│   │   │   ├── displayType
│   │   │   ├── endpointName
│   │   │   ├── status
│   │   │   ├── progress (%)
│   │   │   ├── duration (ms)
│   │   │   ├── createdAt
│   │   │   ├── createdBy
│   │   │   ├── reportUrl
│   │   │   └── metadata
│   │   │       ├── hasCaseDb
│   │   │       ├── hasDroneData
│   │   │       ├── casePpcEntries[]          ← { name, size }
│   │   │       ├── droneZipEntries[]         ← { name, size }
│   │   │       ├── acquisitionProfile { name, id }
│   │   │       └── investigation { status, diskUsageInBytes }
│   │   │
│   │   ├── Task Assignments[]                ← GET /cases/{id}/task-assignments
│   │   │   └── _id                           ← "assignmentId", used as filter in Investigation Hub
│   │   │
│   │   ├── Notes[]                           ← POST/PUT/DELETE /cases/{id}/notes
│   │   ├── Activities[]                      ← GET /cases/{id}/activities
│   │   └── Users[]                           ← GET /cases/{id}/users
│   │
│   └── Policies / Automations / Webhooks     ← org-scoped configuration
│
└── Investigation Hub                         ← keyed by investigationId (from case metadata)
    │
    ├── Investigation                         ← GET /investigation-hub/investigations/{id}
    │   ├── summary                           ← GET .../summary
    │   └── activities[]                      ← GET .../activities
    │
    ├── Assets (Investigation View)           ← GET .../assets
    │   └── Platform Groups[]
    │       ├── platform                      ← "windows" | "linux" | "macos"
    │       └── assets[]
    │           ├── _id                       ← endpoint ID
    │           ├── name                      ← hostname
    │           └── tasks[]
    │               └── _id                   ← assignmentId (used in globalFilter)
    │
    ├── Evidence Sections                     ← POST .../sections
    │   └── Platform Groups[]
    │       ├── platform
    │       └── types[]
    │           └── sections[]
    │               ├── name                  ← evidence category (e.g. "processes", "tcp_table")
    │               └── count                 ← number of rows available
    │
    ├── Evidence Data                         ← POST .../platform/{p}/evidence-category/{c}
    │   ├── totalCount
    │   └── entities[]                        ← the actual evidence rows
    │       ├── air_id                        ← unique row ID
    │       ├── air_task_assignment_id        ← links back to task assignment
    │       ├── air_endpoint_id               ← links back to endpoint
    │       ├── name                          ← e.g. process name, service name
    │       ├── (category-specific columns)   ← varies by evidence type
    │       │
    │       └── (enriched locally by toolkit)
    │           ├── air_endpoint_name         ← resolved from assignment/endpoint ID
    │           └── ingested_at               ← UTC timestamp of download
    │
    ├── Evidence Data Structure               ← GET .../evidence/data-structure
    │   └── (schema/column definitions per evidence category)
    │
    ├── Evidence Counts                       ← GET .../evidence/counts
    │
    ├── Findings (DRONE Analysis)             ← POST .../findings/filter
    │   ├── globalFilter
    │   │   ├── assignmentIds[]
    │   │   ├── findingTypes[]                ← dangerous | suspicious | relevant | matched | rare
    │   │   ├── flagIds[]
    │   │   ├── mitreTechniqueIds[]
    │   │   ├── mitreTacticIds[]
    │   │   └── dateTimeRange
    │   ├── filter[]                          ← { column, operator, value }
    │   ├── skip / take                       ← pagination
    │   └── sort
    │
    ├── Findings Summary                      ← POST .../findings/summary
    │   └── (counts by severity / finding type)
    │
    ├── Flags & Bookmarks                     ← GET/POST .../flags
    │
    ├── Timeline                              ← POST .../timeline/filter
    │   ├── structure                         ← GET .../timeline/structure
    │   └── total-count                       ← GET .../timeline/total-count
    │
    ├── MITRE ATT&CK Matches                 ← POST .../mitre/matches
    │
    ├── Comments[]                            ← GET/POST .../comments
    │
    ├── Exclusion Rules[]                     ← GET/POST .../exclusions
    │
    ├── Advanced Filters[]                    ← saved filter presets
    │
    ├── Reports[]                             ← GET/POST .../reports
    │   └── PDF generation                    ← POST .../reports/{id}/generate-pdf
    │
    ├── Global Search                         ← POST .../global-search
    │
    └── Data Imports[]                        ← POST .../evidence/import
        ├── PST imports
        ├── Tornado imports
        └── import-progress                   ← GET .../import-progress
```

## Key Relationships

```
Organization ──1:N──► Cases
Case ──1:N──► Tasks (acquisitions, triage, etc.)
Case ──1:N──► Endpoints
Case ──1:1──► Investigation (via metadata.investigationId)

Investigation ──1:N──► Platform Groups ──1:N──► Assets ──1:N──► Task Assignments
Investigation ──1:N──► Evidence Sections (by platform + category)
Evidence Section ──1:N──► Evidence Rows

Evidence Row ──► air_task_assignment_id ──► Task Assignment
Evidence Row ──► air_endpoint_id ──► Endpoint/Asset
```

## Toolkit Data Flow

How the scripts in this repo traverse the hierarchy:

```
enumerate_orgs.py          GET /organizations
        │
        ▼
enumerate_cases.py         GET /cases?filter[organizationIds]=N&filter[status]=open
        │
        ▼
case_findings.py           GET /cases/{id}  +  GET /cases/{id}/tasks
case_evidence_structure.py GET /cases/{id}/endpoints  +  Investigation Hub probes
case_extract_findings.py   Probes case + Investigation Hub endpoints
        │
        ▼
case_download_evidence.py  POST .../sections              ← list what's available
                           GET  .../assets                 ← get assignmentIds
                           POST .../platform/{p}/evidence-category/{c}
                                                           ← paginated download
                                                           ← stream to SQLite
        │
        ▼
wrkfl_process_analysis.py  Same download flow, then:
                           SELECT name, COUNT(*) ... GROUP BY name
                           ← frequency analysis on local SQLite
```

## SQLite Output Schema (case_download_evidence.py)

```
evidence.db
├── _checkpoints                     ← resume tracking
│   ├── table_name       TEXT PK
│   ├── investigation_id TEXT
│   ├── last_skip        INTEGER
│   ├── total_count      INTEGER
│   └── updated_at       TEXT
│
└── {evidence_category}              ← one table per category (e.g. "processes")
    ├── air_id                  TEXT  ← unique row identifier
    ├── air_task_assignment_id  TEXT  ← links to task assignment
    ├── air_endpoint_id         TEXT  ← links to endpoint
    ├── air_endpoint_name       TEXT  ← resolved hostname (enriched)
    ├── name                    TEXT  ← primary field (process name, etc.)
    ├── (dynamic columns)            ← varies by evidence category
    ├── ingested_at             TEXT  ← UTC ISO timestamp (enriched)
    │
    ├── UNIQUE INDEX (air_id, air_task_assignment_id)  ← dedup
    └── INDEXES on air_endpoint_name, name, air_endpoint_id, ingested_at
```
