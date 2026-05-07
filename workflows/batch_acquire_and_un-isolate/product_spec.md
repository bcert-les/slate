## Developer-ready API script spec

### 1) Goal

Build a **Python script invoked from Palo Alto SOAR 6.14** to safely orchestrate Binalyze actions from SOAR playbooks. Primary goals are safer execution, better control, and support for workflows not available in the current marketplace integration.

---

## 2) Customer pain points

- **Current SOAR marketplace integration is too limited**
  - Only 2 commands are available.
  - Endpoint control is too restricted for their workflows.
- **Null hostname handling is dangerous**
  - A null hostname can trigger tasks across all endpoints.
  - Customer views this as a critical logic flaw and security risk.
  - They said this issue was flagged **~1.5 years ago** and remains unresolved.
- **No safe guardrails for bulk actions**
  - They need protection against accidental execution across too many endpoints.
  - They want analyst review before continuing large or sensitive actions.
- **Server/critical asset risk**
  - They need server-specific logic so automation does not run freely on critical systems.
- **Need to work entirely from SOAR**
  - Their automation program is centered in SOAR.
  - They are willing to use a Python script, but it must fit into the SOAR playbook flow.
- **On-prem constraints make testing harder**
  - No live screen sharing into the environment.
  - Validation depends on logs, screenshots, and customer-side testing.

---

## 3) Discrete workflows / product use cases

### A. Safe case creation from SOAR

When an alert/incident appears in SOAR, analyst wants to create a corresponding case in Binalyze.

### B. Safe triage/acquisition tasking from SOAR

Analyst wants to create Binalyze tasks from SOAR for relevant endpoints tied to an incident.

### C. Endpoint presence validation before action

Before any task is sent, script should confirm the endpoint exists in Binalyze. If not, return a result so another security tool can be used.

### D. Isolation safety controls

Analyst wants to prevent runaway isolation/acquisition caused by bad input, especially null hostname values or overly broad endpoint targeting.

### E. Analyst approval for sensitive actions

If a target is a server / critical server, or if the target set exceeds 5 endpoints, automation should pause and wait for human approval.

### F. Isolation state management from SOAR

Analyst wants to:

- check whether an endpoint is isolated
- unisolate/de-isolate an endpoint from SOAR after mitigation

---

## 4) Functional requirements

### Required capabilities

1. **Accept execution from SOAR playbook context**.
2. **Create case in Binalyze** from SOAR incident data.
3. **Create task(s) in Binalyze** for endpoint triage/acquisition.
4. **Check endpoint existence in Binalyze** before task creation.
5. **Check endpoint isolation status**.
6. **Support unisolate/de-isolate action**.
7. **Reject null hostname input** and stop safely.
8. **Enforce max batch size of 5 endpoints** before analyst re-approval.
9. **Enforce server/critical-server hold logic** using customer naming convention.
10. **Return actionable status/error output** to SOAR.

---

## 5) Input spec

### Expected inputs

These were not fully defined in the meeting, so this is a proposed interface based on the requested workflows.

```json
{
  "action": "create_case | create_task | check_endpoint | check_isolation | unisolate | batch_execute",
  "incident_id": "string",
  "case_name": "string",
  "case_description": "string",
  "hostnames": ["string"],
  "endpoint_ids": ["string"],
  "task_type": "triage | acquisition | isolation | unisolation",
  "max_batch_size": 5,
  "require_approval_for_servers": true,
  "server_name_patterns": ["string"],
  "require_approval_over_batch_limit": true,
  "approval_token": "string",
  "requested_by": "string"
}
```

### Minimum required fields by action

- **create_case**
  - `incident_id`
  - `case_name`
- **create_task**
  - `task_type`
  - at least one of `hostnames` or `endpoint_ids`
- **check_endpoint**
  - at least one of `hostnames` or `endpoint_ids`
- **check_isolation**
  - at least one of `hostnames` or `endpoint_ids`
- **unisolate**
  - at least one of `hostnames` or `endpoint_ids`
- **batch_execute**
  - `task_type`
  - `hostnames` or `endpoint_ids`
  - `max_batch_size`

---

## 6) Validation rules

### Hard-stop validation

- Reject request if hostname value is null/empty.
- Reject request if target resolution returns zero valid endpoints.
- Reject request if action is unsupported.
- Reject request if required parameters for the selected action are missing.

### Safety validation

- If target count is **> 5**, do not proceed automatically; pause and require analyst approval before next batch.
- If any target matches customer server naming convention, pause and require analyst approval.
- If endpoint is not present in Binalyze, return a clear status so customer can route to another tool.

---

## 7) Control flow

### Workflow 1: create case + task safely

1. Receive incident payload from SOAR.
2. Validate required fields.
3. Validate hostname(s) are non-null.
4. Resolve/check endpoint(s) in Binalyze.
5. If endpoint missing, return `endpoint_not_found`.
6. Check whether any target matches server naming convention.
7. Check target count against batch limit of 5.
8. If approval needed, return `approval_required`.
9. Create Binalyze case.
10. Create Binalyze task(s).
11. Return success payload.

### Workflow 2: check endpoint before action

1. Receive hostname/endpoint.
2. Validate input.
3. Query Binalyze for endpoint existence.
4. Return:
  - found
    - not found
    - ambiguous
    - error

### Workflow 3: check isolation status

1. Receive hostname/endpoint.
2. Validate input.
3. Query isolation state.
4. Return current state.

### Workflow 4: unisolate endpoint

1. Receive hostname/endpoint.
2. Validate input.
3. Check endpoint exists.
4. Check current isolation state.
5. If not isolated, return no-op status.
6. If isolated, execute unisolation.
7. Return result.

### Workflow 5: batch approval flow

1. Receive endpoint set.
2. Validate all hostnames non-null.
3. Split into chunks of 5.
4. Execute first chunk only if no approval gate is triggered.
5. Return pending approval state for remaining chunk(s).

---

## 8) Proposed response schema

```json
{
  "status": "success | error | approval_required | partial_success | no_op",
  "action": "string",
  "message": "string",
  "case_id": "string",
  "task_ids": ["string"],
  "approved": false,
  "requires_approval": true,
  "matched_server_pattern": true,
  "target_count": 7,
  "processed_count": 5,
  "remaining_count": 2,
  "missing_endpoints": ["string"],
  "invalid_inputs": ["string"],
  "details": {}
}
```

---

## 9) Error cases

### Must be explicit

- `null_hostname`
- `missing_required_field`
- `endpoint_not_found`
- `too_many_targets_requires_approval`
- `server_target_requires_approval`
- `unsupported_action`
- `endpoint_already_unisolated`
- `api_request_failed`
- `authentication_failed`
- `ambiguous_endpoint_match`

These should be returned as structured outputs to SOAR, not hidden in logs.

---

## 10) Logging requirements

Given the on-prem testing model, logging should be first-class.

### Log at minimum

- request action
- request timestamp
- target count
- validation failures
- approval gate triggered or not
- Binalyze API endpoint called
- response status
- correlation ID / incident ID
- sanitized error details

### Must support customer troubleshooting with:

- exported logs
- screenshots of on-screen errors
- enough detail to diagnose without screen sharing

---

## 11) Assumptions / unresolved details

These were **not fully specified** in the meeting and need confirmation.

- Exact Binalyze API endpoints for:
  - case creation
  - task creation
  - endpoint lookup
  - isolation status
  - unisolation
- Exact SOAR method for passing approval state back into the script
- Exact server naming patterns
- Whether hostname or endpoint ID is the true primary key
- Whether batching should:
  - stop after each 5
  - or auto-prepare next 5 and wait for approval
- Exact format of success/error objects expected by Palo Alto SOAR
- Whether “create task” means triage only, acquisition only, or both depending on playbook branch

---

## 12) Suggested acceptance criteria

### Safety

- Script never executes if hostname is null.
- Script never auto-processes more than 5 endpoints without approval.
- Script always pauses on server/critical-server targets.

### Functionality

- Can create a Binalyze case from SOAR input.
- Can create Binalyze task(s) from SOAR input.
- Can check whether endpoint exists.
- Can check whether endpoint is isolated.
- Can unisolate endpoint.

### Operability

- Errors are readable and structured.
- Logs are sufficient for customer-side testing without live screen share.

---

## 13) Recommended next clarification questions

- What exact actions should `create_task` support on day 1: triage, acquisition, isolation, or all three?
- What hostname patterns define a server / critical server?
- What should the approval handshake look like in SOAR?
- Should endpoint existence checks use hostname only, or hostname + org/context?
- What exact output format does their SOAR playbook expect?
- Should the script create the case first, or only after endpoint validation passes?

If you want, I can convert this next into a **one-page product requirements document** or a **technical handoff doc for engineering**.