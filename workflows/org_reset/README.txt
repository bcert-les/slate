# Org Reset Workflow (org_reset.py)

## Overview

The `org_reset.py` workflow provides a safe and repeatable method for resetting a Binalyze AIR training organization to a "Day 1" state.

The workflow is intended for training, certification, workshop, and lab environments where investigators need to repeatedly return an organization to a known baseline without affecting system configuration, assets, policies, or evidence repositories.

### Current Actions

The workflow performs the following actions:

1. Enumerates all cases in the specified organization.
2. Closes all open cases.
3. Enumerates all Hunt/Triage rules.
4. Deletes all non-system-created Hunt/Triage rules.
5. Preserves:

   * Assets
   * Policies
   * Acquisition Profiles
   * Repositories
   * System Hunt/Triage Rules
   * Users
   * Organization configuration

The workflow does **not** uninstall agents, purge evidence, modify assets, or change organizational settings.

---

## Prerequisites

### Environment Variables

A valid `.env` file must exist in the Updraft project root.

Example:

```env
BINALYZE_AIR_HOST=https://your-air-instance.binalyze.com
BINALYZE_API_TOKEN=api_xxxxxxxxxxxxxxxxxxxxxxxxx
```

### Python Dependencies

Install required packages:

```bash
pip install -r requirements.txt
```

---

## Usage

### Preview Changes (Recommended)

Perform a dry run without making any modifications:

```bash
python workflows/org_reset/org_reset.py --org-id 362 --dry-run
```

Example output:

```text
Org reset target: 362
Mode: DRY RUN

Cases found: 8
Open cases to close: 8

Triage rules found: 12
Non-system triage rules to delete: 7
```

No changes will be made.

---

### Execute Reset

To perform the reset:

```bash
python workflows/org_reset/org_reset.py --org-id 362 --yes
```

Example:

```text
Org reset target: 362
Mode: LIVE CHANGES

Closing case: Investigation 001
Closing case: Malware Analysis

Deleting triage rule: Sigma-001
Deleting triage rule: YARA-001
```

---

## Safety Controls

### Dry Run by Default

The workflow is designed to preview actions unless explicitly authorized using:

```bash
--yes
```

### Organization Scope

Only the specified organization ID is modified.

Example:

```bash
--org-id 362
```

### System Rule Protection

System-created Hunt/Triage rules are preserved.

The workflow only removes user-created rules.

---

## Recommended Training Workflow

Prior to each training session:

```bash
python workflows/org_reset/org_reset.py \
  --org-id 362 \
  --dry-run
```

Review the planned changes.

If the output is correct:

```bash
python workflows/org_reset/org_reset.py \
  --org-id 362 \
  --yes
```

This returns the training organization to a clean state while preserving core platform functionality.

---

## Suggested Future Enhancements

The following optional features may be added in future versions:

### Case Archiving Report

Generate a CSV summary of all cases closed during the reset.

### Rule Backup

Export Hunt/Triage rules to JSON before deletion.

### Acquisition Cleanup

Cancel pending acquisition or hunt tasks before closing cases.

### Asset Tag Cleanup

Remove temporary training tags from endpoints.

### Policy Reset

Restore approved training policies from a baseline configuration.

### Baseline Validation

Verify that the organization matches an expected Day 1 configuration before declaring reset complete.

---

## Example

Reset the CBX Ransomware training organization:

```bash
python workflows/org_reset/org_reset.py \
  --org-id 362 \
  --yes
```

Result:

```text
✓ All open cases closed
✓ All user-created Hunt/Triage rules removed
✓ System rules preserved
✓ Assets preserved
✓ Policies preserved
✓ Organization ready for next training class
```

---

## Author

Updraft Workflow

Purpose:
Provide a safe, repeatable mechanism for restoring Binalyze AIR training organizations to a known baseline state for workshops, certifications, demonstrations, and lab exercises.
