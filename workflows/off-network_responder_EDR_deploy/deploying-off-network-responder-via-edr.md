---
title: Deploying the Off-Network Responder via an EDR Remote Execution Channel
description: >-
  A vendor-neutral workflow for delivering, running, and retrieving the
  $Product Off-Network Responder on isolated or quarantined endpoints by
  using an EDR's remote script execution, file distribution, remote shell,
  and file retrieval capabilities.
---


## 1. Purpose

This article provides a generic, EDR-assisted deployment workflow for the $Product **Off-Network Responder**. The $Product steps are product-supported and documented in the linked $Product KB articles. The EDR-specific steps depend on the customer's EDR platform, license, policy configuration, and administrative permissions. SentinelOne examples are included to illustrate common implementation patterns, but customers should validate exact steps, limits, and permissions with their EDR administrator or MSP.

Use this workflow when responders must collect forensic evidence from endpoints that have already been network-isolated by an EDR or by policy, and where the normal $Product Responder deployment routes (Console URL reachable, Active Directory, RMM, or network shares) are not available.

## 2. When to use this workflow

Use this workflow when **one or more** of the following is true:

- The endpoint has been isolated or quarantined by an EDR or by incident-response policy and cannot reach the $Product Console directly.
- Network shares, RDP, and standard remote deployment paths are blocked.
- The customer's EDR can still reach the endpoint to execute scripts, distribute files, open a remote shell session, or retrieve files.
- The incident is time-critical (for example, an active ransomware engagement) and standard deployment cannot wait for network re-enablement.
- Specific high-value assets (file servers, domain controllers, backup servers) must be triaged before isolation is lifted.

## 3. When **not** to use this workflow

Do **not** use this workflow when:

- The endpoint can still communicate with the $Product Console. In that case, deploy a normal Responder using the standard methods described in [Responder Deployment](../../setup/responder-deployment/) and create an online task instead.
- A bootable USB and physical access are available, and live OS collection is not possible. Use the bootable-media flow described in [Off-Network Responder](./) (WinRE runbook) instead.
- Your EDR vendor or MSP explicitly prohibits third-party binary execution on isolated hosts during an active incident, and no exception has been granted.

## 4. Key concept

The workflow is split into two clearly separated halves:

| Layer | Owned by | Responsibilities |
|---|---|---|
| **Forensics payload** | $Product (Off-Network Responder) | Package generation, evidence collection, evidence container creation, encryption, and import back into the $Product Console. |
| **Transport / execution channel** | The customer's EDR | Delivering files to the isolated endpoint, executing the binary, retrieving the resulting evidence container, and (where required) granting temporary exclusions. |

$Product does not officially integrate with any specific EDR's remote execution feature. The EDR is treated only as a **transport, execution, and retrieval channel**. Every EDR-specific step (limits, permissions, allow-listing, file size caps, timeouts) must be confirmed in the customer's own tenant.

## 5. High-level flow

1. **Investigation initiated.** Scope is defined and target endpoints are identified.
2. **Pre-Setup.** Generate the Off-Network Responder package from the $Product Console, record file hashes, and prepare temporary EDR exclusions if required.
3. **Deploy.** Push the package to the isolated endpoint using any EDR-supported method (file distribution, base64-in-script, remote shell, approved network share/SFTP, or manual transfer).
4. **Collection.** Run the Off-Network binary on the endpoint, monitor progress, and validate that the evidence container (`.zip` or `.ppc`) is produced.
5. **Output Retrieval.** Pull the evidence container off the endpoint using EDR file retrieval, an approved network share or SFTP, or manual transfer.
6. **AIR Import.** Import the evidence container into the **same** $Product Console that generated the package, and continue analysis in the Investigation Hub.

```text
[ AIR Console ] --(generate package)--> [ EDR transport ] --(deliver/run)--> [ Isolated endpoint ]
       ^                                                                            |
       |                                                                            v
       +<------(import .zip / .ppc)<-----[ EDR retrieval ]<------(produce evidence container)
```

## 6. Step-by-step workflow

### 6.1 Download the Off-Network Responder package

From the $Product Console, create an Off-Network task and download the Responder package for the target operating system(s). See the full procedure in [Off-Network Responder](./).

Key points:

- Always download the package from the **same Console** that will later import the resulting evidence. Off-Network containers can only be imported by the originating Console.
- Pick the right operating-system binary (Windows / Linux / macOS). When in doubt, generate a multi-OS package.
- Choose an [Acquisition Profile](../acquisition/acquisition-profiles) appropriate to the engagement (full forensic, triage, IR-focused, etc.).
- For sensitive engagements, enable the optional password during Responder generation. You will need this password during import; the [biunzip](./biunzip/) utility helps when importing many encrypted containers at once.

### 6.2 Record binary or package hashes (recommended)

Before transferring the package, record cryptographic hashes (SHA-256) of:

- The Off-Network binary (for example `offnetwork_windows_amd64.exe`).
- The full distribution archive, if you plan to send a ZIP.

These hashes let the responder verify the file on the endpoint, prove chain of custody, and document exactly which payload was executed.

### 6.3 Prepare temporary EDR exclusions (only if required)

In some environments the EDR or AV will block or delete the Off-Network binary as an unknown executable. If that happens, prepare **temporary**, **scoped** exclusions. Wherever possible, use:

- The folder where the package will land on the endpoint, **or**
- A hash- or signer-based exclusion, **or**
- The recommended Responder paths listed in [Responder Exception Rules for EPP and EDR](../../setup/responder-deployment/responder-exception-rules/).

Exclusions must be:

- Limited to the target endpoint(s) or a dedicated investigation group, not the whole estate.
- Time-boxed to the duration of the engagement.
- Removed once evidence has been retrieved and imported successfully.

If the MSP or EDR administrator does not allow broad allow-listing or policy changes, fall back to a deployment method that does not require persistent disk presence (for example, base64-in-script with execution from a temp directory).

### 6.4 Push the package to the endpoint

Use whichever EDR transport is available. The four most common options are summarised below; choose based on what your EDR license, policy, and engagement allow.

#### Option A — EDR payload or file distribution

Many EDRs let you upload an arbitrary file or ZIP and ship it to the endpoint alongside the script that runs it. This is the cleanest path when supported.

- **SentinelOne RemoteOps Custom Script Actions** — Upload a PowerShell or Bash script plus an optional payload (binary, additional scripts, installers, configuration files) to the RemoteOps Script Library. When the action runs, the SentinelOne agent makes the package available at the path stored in the `S1_PACKAGE_DIR_PATH` environment variable, which your script can reference to locate and launch the bundled files. See the SentinelOne reference in section 10.
- **CrowdStrike Real Time Response (RTR)** — Upload a file to the **put-file** library and use `put` (to stage the file on the endpoint) or `put-and-run` (to stage and execute it in a single step). Capabilities such as `put`, `put-and-run`, and custom scripts must be enabled in the matching Response Policy.
- **Microsoft Defender for Endpoint Live Response** — Upload the file to the Live Response **library**, then use `putfile` to copy it to the device and `run` to execute a PowerShell or Bash script from the library.

:::note
Each EDR has its **own** limits on payload size, script size, runtime, and concurrent sessions. These are not interchangeable between products. Validate them with your EDR administrator before relying on this path for a full Off-Network package.
:::

#### Option B — Base64-encoded ZIP through PowerShell or remote script execution

When your EDR only supports script execution (no separate file upload), you can embed the Off-Network package as a base64 string inside the script. The script decodes it on the endpoint, writes the ZIP to a temp folder, expands it, and runs the Responder.

Conceptual flow (Windows / PowerShell):

```powershell
$Pkg = "<BASE64_ZIP_HERE>"

$Work = Join-Path $env:TEMP "air-offnet"
New-Item $Work -ItemType Directory -Force | Out-Null

[IO.File]::WriteAllBytes("$Work\air-offnet.zip",
    [Convert]::FromBase64String($Pkg))

Expand-Archive "$Work\air-offnet.zip" -DestinationPath $Work -Force
& "$Work\offnetwork_windows_amd64.exe"
```

Use this approach with caution:

- Embedded base64 inflates the script roughly 33%. The combined script can easily exceed your EDR's per-script size limit.
- Pushing very large encoded payloads through chat-style remote-shell sessions is fragile (line buffers, paste limits, timeouts).
- The full PowerShell script used in production should include error handling, logging, hash verification, and explicit cleanup. Keep that script under change control rather than reproducing it verbatim in this article.

This option works well for **a small number of critical assets** (for example, file servers or domain controllers) but is not ideal for full-environment deployment.

#### Option C — Remote shell with manual staging

If the EDR provides an interactive remote shell (for example, **SentinelOne Full Remote Shell**, **CrowdStrike RTR**, or **MDE Live Response**), an analyst can:

1. Open a remote shell to the endpoint.
2. Stage the Off-Network package on the endpoint using the EDR's own file-staging primitives (for example, `put` in RTR, `putfile` in MDE Live Response, or a script that downloads from an approved location).
3. Expand the archive and launch the binary manually.
4. Watch the output and confirm the evidence container is produced.

Remote shells are usually subject to **idle and total-session timeouts** (for example, 30 minutes in some products). Long Off-Network collections may outlive a single session, so plan to detach and reconnect, or run the binary as a background process.

#### Option D — Approved network share, SFTP, or repository

When partial network access is allowed, you can host the package on:

- An approved internal SMB share that the isolated host can still reach.
- An SFTP or HTTPS endpoint operated by the IR team or MSP.
- An $Product [Evidence Repository](../evidence-repositories/) reachable from the endpoint.

The EDR is then only used to execute a small command that pulls the package from the agreed location. This is operationally cleaner than embedding base64 but requires that the isolation policy permits the chosen destination.

#### Option E — Manual transfer (last resort)

Where no remote channel is usable, transport the package by physical media (USB) or via on-site personnel. This bypasses the EDR entirely but is the slowest and least scalable option. It also requires strong custody controls (sealed media, signed handover, hash verification).

### 6.5 Confirm the package is present on the endpoint

Before executing, verify:

- The Off-Network binary and its companion `Task.dat` file are present **in the same directory**.
- The on-disk file hash matches the hash recorded in step 6.2.
- The current user (or the EDR's execution context) has the privileges listed in [Off-Network Responder](./): local administrator on Windows, root on Linux, admin with sudo on macOS.

### 6.6 Execute the package

Launch the binary from its working directory. The Responder will follow the embedded `Task.dat` and the chosen Acquisition Profile. Collection time depends on the profile, asset size, and CPU/IO limits configured during package creation.

If you need to write the case output to a different directory (for example, a drive with more free space), use the `--case-base-dir` flag described in [Setting Up a Custom Case Directory](./setting-up-a-custom-case-directory).

### 6.7 Monitor collection and validate the evidence container

Through your chosen EDR channel:

- Tail the Responder output or its log file to confirm progress.
- Wait for a clear completion message before assuming the run is finished.
- Confirm the `Cases` folder exists in the working directory and contains the expected `.zip` (or `.ppc`) file.

Record the evidence file's size and SHA-256 hash for chain-of-custody documentation before retrieving it.

### 6.8 Retrieve the evidence container

Pick a retrieval channel that matches how you delivered the package:

| Delivery channel used | Typical retrieval channel |
|---|---|
| EDR file distribution / script | EDR file retrieval (for example, SentinelOne **Fetch Files**, CrowdStrike **get**, MDE **getfile**) |
| Base64-in-script | EDR file retrieval **or** approved share / SFTP |
| Remote shell | EDR file retrieval **or** approved share / SFTP |
| Approved network share / SFTP | The same share or SFTP path |
| Manual transfer | Manual transfer back |

For all EDR-native retrieval mechanisms, validate in advance:

- The maximum file size the EDR will accept for a single retrieval (varies widely by product and license).
- Whether the retrieved file is encrypted or wrapped (most EDR fetch features return a password-protected archive).
- How long it takes to upload a multi-GB evidence container over the agent channel. Where the evidence container is large, an approved share or SFTP is usually faster than the EDR's own pipe.

### 6.9 Notify and update the case

Once the evidence container has been retrieved successfully:

- Update the case management system (or the parent ticket) with the asset name, package hash, container hash, time of execution, and the operator who performed the action.
- Notify the engagement lead that the collection is in hand and ready for import.

### 6.10 Remove temporary exclusions

If any EDR exclusions were created in step 6.3, **revert them as soon as evidence is retrieved and imported successfully**. Do not leave temporary allow-listing in place after the engagement.

### 6.11 Import the container into the $Product Console

Import the retrieved `.zip` or `.ppc` file into the **same** $Product Console that generated the Off-Network package. The full import procedure (including DRONE re-analysis, password entry, and bulk import using [biunzip](./biunzip/)) is documented in [Off-Network Responder](./).

After import, continue analysis in the [Investigation Hub](../investigation-hub/).

### 6.12 Continue analysis

Triage findings, pivot across artefacts, apply [auto-tagging](../auto-tagging-and-tags/), and (if applicable) feed indicators into wider Hunt or Triage activity across the rest of the estate.

## 7. Deployment method summary

| Method | Best used when | Notes | Example platform capability |
|---|---|---|---|
| **EDR payload or file distribution** | The EDR supports uploading an arbitrary file or ZIP alongside the script. | Cleanest path. Subject to per-product payload-size and timeout limits. Validate in tenant. | SentinelOne RemoteOps Custom Script Actions (with `S1_PACKAGE_DIR_PATH`); CrowdStrike RTR `put` / `put-and-run`; MDE Live Response `putfile` from the library. |
| **Base64-encoded ZIP in a script** | The EDR only supports script execution, and only a small number of critical assets are in scope. | Fragile for large packages. The combined script can hit per-script size limits. Keep the production script under change control. | SentinelOne RemoteOps script; MDE Live Response `run`; CrowdStrike RTR `runscript`. |
| **Remote shell** | An analyst can sit on the endpoint long enough to stage and launch interactively. | Session timeouts (often around 30 minutes) may not cover full collections. Plan to detach or run in background. | SentinelOne Full Remote Shell; CrowdStrike RTR session; MDE Live Response session. |
| **Network share or SFTP** | Partial network access is permitted and reaches the isolated host. | Operationally cleaner than base64. Requires policy approval for the destination. | Internal SMB share, SFTP server, or an $Product Evidence Repository. |
| **Manual transfer** | No remote channel is usable. | Slowest; needs strong custody controls (sealed media, signed handover, hash verification). | USB / on-site personnel. |

## 8. Customer validation checklist

Validate **all** of the following with the customer's EDR administrator (or MSP) **before** committing to this workflow for an engagement.

| Area | What to confirm |
|---|---|
| **EDR access** | Do responders have an EDR account with the right role to run scripts, open remote shells, push files, and retrieve files on the in-scope tenant? |
| **Isolation behavior** | When the EDR isolates a host, does it still allow agent-channel script execution, file distribution, and file retrieval? |
| **File delivery** | Which file-distribution primitive will be used (RemoteOps payload, RTR `put`/`put-and-run`, MDE `putfile`, etc.)? What is the maximum payload size? |
| **Base64 fallback** | If file distribution is not available, is in-script base64 acceptable? What is the maximum script size? |
| **Script and payload size limits** | Confirm both script-size and payload-size limits in writing for the platform/license in use. |
| **Execution timeout** | Confirm per-command and per-session timeouts. Are they long enough for the chosen Acquisition Profile? |
| **Execution privileges** | Confirm the EDR runs scripts as SYSTEM / root and that this satisfies the [Off-Network Responder](./) privilege requirement. |
| **Temporary exclusions** | If exclusions are required, what is the change-control process? Who approves and reverts them? |
| **Evidence output** | Confirm where the Responder will write the evidence container (default vs `--case-base-dir`) and that the volume has enough free space. |
| **File retrieval** | What is the maximum size for EDR file retrieval (Fetch Files / `get` / `getfile`)? Is there a faster out-of-band channel for large containers? |
| **AIR import** | Confirm the $Product Console that will receive the container is the **same** Console that generated the package, and that any Off-Network password is captured in case notes. |

## 9. Security and operational considerations

- **Use least privilege and least scope.** Only the responders who need to execute and retrieve the package should hold the necessary EDR roles for the engagement.
- **Scope temporary exclusions narrowly.** Apply exclusions only to target endpoints (or a dedicated investigation group) and only for the investigation window.
- **Prefer hash- or signer-based exclusions** over broad folder or process exclusions, where the EDR supports them.
- **Avoid broad permanent exclusions.** Anything created for this workflow is temporary and must be reverted in step 6.10.
- **Validate file hashes** on both ends of the transfer — before delivery, after delivery on the endpoint, and again after retrieval — so any in-transit corruption or tampering is caught.
- **Preserve logs and chain-of-custody data.** Capture EDR session transcripts, command outputs, hashes, timestamps, and the operator identity. Store them with the engagement record.
- **Do not remove temporary files** (Off-Network binary, intermediate archives, working directories) on the endpoint until the evidence container has been retrieved **and** successfully imported into the $Product Console.
- **Encrypt where possible.** Use the Off-Network Responder password feature for sensitive engagements, and prefer encrypted transports (SFTP, HTTPS) over plain SMB where it is available.
- **Respect MSP boundaries.** If the customer's environment is co-managed, agree the workflow, exclusions, and roles with the MSP in writing before executing.

## 10. Reference URLs

### $Product (official)

- Off-Network Responder — <https://kb.binalyze.com/air/features/off-network-endpoint>
- Responder Deployment — <https://kb.binalyze.com/air/setup/responder-deployment>
- Responder Exception Rules for EPP and EDR — <https://kb.binalyze.com/air/setup/responder-deployment/responder-exception-rules>
- Acquisition Profiles — <https://kb.binalyze.com/air/features/acquisition/acquisition-profiles>
- Evidence Repositories — <https://kb.binalyze.com/air/features/evidence-repositories>
- Setting Up a Custom Case Directory — <https://kb.binalyze.com/air/features/off-network-endpoint/setting-up-a-custom-case-directory>
- Offline collection with AIR (blog) — <https://www.binalyze.com/blog/dfir-lab/offline-collection-with-air>

### SentinelOne (example EDR — official sources)

- Feature Spotlight | Introducing RemoteOps Custom Script Actions — <https://www.sentinelone.com/blog/feature-spotlight-introducing-remoteops-custom-script-actions/>
- Full Remote Shell — <https://www.sentinelone.com/blog/full-remote-shell/>
- SentinelOne Releases Full Remote Shell Capabilities (press) — <https://www.sentinelone.com/press/sentinelone-releases-full-remote-shell-capabilities/>
- Singularity Complete (platform page, lists remote shell, file fetch) — <https://www.sentinelone.com/platform/singularity-complete/>

### Other EDR / XDR remote response references (official)

- Microsoft Defender for Endpoint — Live Response overview — <https://learn.microsoft.com/en-us/defender-endpoint/live-response>
- Microsoft Defender for Endpoint — Live Response command examples — <https://learn.microsoft.com/en-us/defender-endpoint/live-response-command-examples>
- Microsoft Defender for Endpoint — Run Live Response commands API — <https://learn.microsoft.com/en-us/defender-endpoint/api/run-live-response>
- CrowdStrike Real Time Response (RTR) policy reference (Terraform docs) — <https://registry.terraform.io/providers/CrowdStrike/crowdstrike/latest/docs/resources/response_policy>
- CrowdStrike FalconPy — Real Time Response Admin service collection — <https://www.falconpy.io/Service-Collections/Real-Time-Response-Admin.html>

### Supporting technical references (community / third-party — supporting context only)

- SentinelOne v2 integration (Cortex XSOAR) — documents `sentinelone-fetch-file` and `sentinelone-download-fetched-file` flows — <https://xsoar.pan.dev/docs/reference/integrations/sentinel-one-v2>
- Cyber Triage — SentinelOne-based Collections — documents practical RemoteOps timeout / output-size guidance (third-party) — <https://docs.cybertriage.com/en/latest/chapters/integrations/s1_collect.html>
- Hexastrike — Velociraptor + CrowdStrike RTR (community write-up; treat as supporting context) — <https://hexastrike.com/resources/blog/dfir/combining-the-raptors-incident-response-using-velociraptor-and-crowdstrike-falcon/>

:::note
Third-party links above are listed as supporting context only. Always validate exact behaviour, limits, and command syntax against the customer's current EDR documentation and tenant configuration.
:::
