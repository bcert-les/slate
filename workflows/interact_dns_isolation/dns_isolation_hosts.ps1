#Requires -RunAsAdministrator
<#
.SYNOPSIS
  Supplemental DNS isolation via the Windows hosts file for Binalyze AIR endpoints.

.DESCRIPTION
  Pins the Binalyze AIR console hostname to its resolved IP so the responder agent
  keeps console connectivity, while sinkholing configured hostnames (default: DNS
  bypass / DoH providers) to 0.0.0.0.

  Intended for manual deployment through Binalyze InterACT remote shell on isolated
  or suspect Windows endpoints. This does not replace Binalyze network isolation;
  it adds a DNS-layer control that survives until explicitly reverted.

  All changes are wrapped in marker comments for idempotent enable/disable.

.PARAMETER Action
  Enable  - Apply or refresh the managed hosts block.
  Disable - Remove the managed block and restore the pre-enable backup when available.
  Status  - Show whether the managed block is present and list its entries.

.PARAMETER ConsoleHostname
  AIR console FQDN (e.g. your-tenant.binalyze.com). When omitted, the script reads
  the responder config.yml from standard Binalyze agent install paths.

.PARAMETER ConsoleIp
  Optional. Pin the console to this IPv4 address instead of live DNS resolution.
  Use when the endpoint is already isolated and you need a known-good console IP.

.PARAMETER AllowedHostnames
  Additional hostnames to pin to their current resolved IPv4 (relay servers, evidence
  repositories, etc.). Console hostname is always allowed.

.PARAMETER BlockListPath
  Path to a text file of hostnames to sinkhole (one per line; # comments allowed).
  Defaults to default_blocklist.txt alongside this script.

.PARAMETER SkipDefaultBlockList
  Do not load the bundled default blocklist (console pinning still applies).

.EXAMPLE
  .\dns_isolation_hosts.ps1 -Action Enable

.EXAMPLE
  .\dns_isolation_hosts.ps1 -Action Enable -ConsoleHostname tenant.binalyze.com -ConsoleIp 203.0.113.10

.EXAMPLE
  .\dns_isolation_hosts.ps1 -Action Disable

.EXAMPLE
  .\dns_isolation_hosts.ps1 -Action Status
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [ValidateSet('Enable', 'Disable', 'Status')]
    [string] $Action = 'Enable',

    [string] $ConsoleHostname,

    [string] $ConsoleIp,

    [string[]] $AllowedHostnames = @(),

    [string] $BlockListPath,

    [switch] $SkipDefaultBlockList,

    [switch] $Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$MarkerBegin = '# BEGIN BINALYZE-DNS-ISOLATION'
$MarkerEnd   = '# END BINALYZE-DNS-ISOLATION'
$HostsPath   = Join-Path $env:SystemRoot 'System32\drivers\etc\hosts'
$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackupPath  = Join-Path $ScriptDir 'hosts.pre-binalyze-dns-isolation.bak'

function Write-Log {
    param([string] $Message)
    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Write-Output "[$stamp] $Message"
}

function Get-AgentConfigPaths {
    $paths = @(
        (Join-Path ${env:ProgramFiles(x86)} 'binalyze\agent\config.yml'),
        (Join-Path $env:ProgramFiles 'binalyze\agent\config.yml'),
        (Join-Path ${env:ProgramFiles(x86)} 'binalyze\agent\config.yaml'),
        (Join-Path $env:ProgramFiles 'binalyze\agent\config.yaml')
    )
    return $paths | Where-Object { Test-Path -LiteralPath $_ }
}

function Get-ConsoleHostnameFromAgentConfig {
    foreach ($configPath in (Get-AgentConfigPaths)) {
        Write-Log "Reading agent config: $configPath"
        $content = Get-Content -LiteralPath $configPath -Raw -ErrorAction Stop

        $patterns = @(
            '(?im)^\s*(?:console(?:Address|Url|Host|Server)|server(?:Url|Address|Host)|api(?:Url|Host)|host)\s*:\s*["'']?(https?://)?([^/"''\s]+)',
            '(?im)["''](https?://([^/"''\s]+))["'']'
        )

        foreach ($pattern in $patterns) {
            $match = [regex]::Match($content, $pattern)
            if ($match.Success) {
                $hostCandidate = $match.Groups[$match.Groups.Count - 1].Value.Trim().TrimEnd('/')
                if ($hostCandidate -match '^[\w\.-]+$') {
                    Write-Log "Detected console hostname from agent config: $hostCandidate"
                    return $hostCandidate.ToLowerInvariant()
                }
            }
        }
    }

    return $null
}

function Resolve-HostnameToIPv4 {
    param([Parameter(Mandatory = $true)][string] $Hostname)

    $hostname = $Hostname.Trim().ToLowerInvariant()
    if ($hostname -match '^\d{1,3}(\.\d{1,3}){3}$') {
        return $hostname
    }

    $addresses = [System.Net.Dns]::GetHostAddresses($hostname)
    $ipv4 = $addresses | Where-Object { $_.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork } | Select-Object -First 1
    if (-not $ipv4) {
        throw "No IPv4 address resolved for '$hostname'."
    }
    return $ipv4.ToString()
}

function Read-BlockListFile {
    param([string] $Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Block list not found: $Path"
    }

    $entries = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    Get-Content -LiteralPath $Path | ForEach-Object {
        $line = $_.Split('#')[0].Trim()
        if ($line) {
            [void]$entries.Add($line.ToLowerInvariant())
        }
    }
    return $entries
}

function Get-ManagedBlockLines {
    param(
        [hashtable] $AllowedEntries,
        [System.Collections.Generic.HashSet[string]] $BlockedHostnames
    )

    $lines = New-Object System.Collections.Generic.List[string]
    [void]$lines.Add($MarkerBegin)
    [void]$lines.Add("# Applied: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss K')")
    [void]$lines.Add("# Purpose: Pin AIR console for agent connectivity; sinkhole DNS bypass domains.")
    [void]$lines.Add('')

    [void]$lines.Add('# Allowed — pinned to resolved IPv4 (agent / console path)')
    foreach ($hostname in ($AllowedEntries.Keys | Sort-Object)) {
        [void]$lines.Add("$($AllowedEntries[$hostname])`t$hostname")
    }

    if ($BlockedHostnames.Count -gt 0) {
        [void]$lines.Add('')
        [void]$lines.Add('# Blocked — sinkholed to 0.0.0.0')
        foreach ($hostname in ($BlockedHostnames | Sort-Object)) {
            foreach ($allowed in $AllowedEntries.Keys) {
                if ($hostname -eq $allowed) {
                    throw "Block list contains allowed hostname '$hostname'. Remove it from the block list."
                }
            }
            [void]$lines.Add("0.0.0.0`t$hostname")
        }
    }

    [void]$lines.Add($MarkerEnd)
    return $lines
}

function Remove-ManagedBlock {
    param([string[]] $ContentLines)

    $output = New-Object System.Collections.Generic.List[string]
    $inside = $false

    foreach ($line in $ContentLines) {
        if ($line -eq $MarkerBegin) {
            $inside = $true
            continue
        }
        if ($line -eq $MarkerEnd) {
            $inside = $false
            continue
        }
        if (-not $inside) {
            [void]$output.Add($line)
        }
    }

    return ,$output.ToArray()
}

function Get-ManagedBlockFromContent {
    param([string[]] $ContentLines)

    $inside = $false
    $block = New-Object System.Collections.Generic.List[string]

    foreach ($line in $ContentLines) {
        if ($line -eq $MarkerBegin) {
            $inside = $true
            continue
        }
        if ($line -eq $MarkerEnd) {
            break
        }
        if ($inside) {
            [void]$block.Add($line)
        }
    }

    return ,$block.ToArray()
}

function Confirm-Action {
    param([string] $Prompt)

    if ($Force) {
        return $true
    }

    $answer = Read-Host "$Prompt [y/N]"
    return $answer -match '^(y|yes)$'
}

function Show-Status {
    if (-not (Test-Path -LiteralPath $HostsPath)) {
        Write-Log "Hosts file not found: $HostsPath"
        return
    }

    $content = Get-Content -LiteralPath $HostsPath
    $block = Get-ManagedBlockFromContent -ContentLines $content

    if ($block.Count -eq 0) {
        Write-Log "Managed Binalyze DNS isolation block is NOT present."
        return
    }

    Write-Log "Managed Binalyze DNS isolation block IS present:"
    $block | ForEach-Object { Write-Output $_ }

    if (Test-Path -LiteralPath $BackupPath) {
        Write-Log "Pre-enable backup exists: $BackupPath"
    }
}

function Enable-DnsIsolation {
    if (-not $ConsoleHostname) {
        $ConsoleHostname = Get-ConsoleHostnameFromAgentConfig
    }

    if (-not $ConsoleHostname) {
        throw "Console hostname not provided and could not be read from agent config.yml. Pass -ConsoleHostname."
    }

    $ConsoleHostname = $ConsoleHostname.Trim().ToLowerInvariant()
    Write-Log "Console hostname: $ConsoleHostname"

    if ($ConsoleIp) {
        $consoleResolvedIp = $ConsoleIp.Trim()
        Write-Log "Using supplied console IP: $consoleResolvedIp"
    }
    else {
        $consoleResolvedIp = Resolve-HostnameToIPv4 -Hostname $ConsoleHostname
        Write-Log "Resolved console IP: $consoleResolvedIp"
    }

    $allowed = @{}
    $allowed[$ConsoleHostname] = $consoleResolvedIp

    foreach ($extra in $AllowedHostnames) {
        $extraHost = $extra.Trim().ToLowerInvariant()
        if (-not $extraHost) { continue }
        if (-not $allowed.ContainsKey($extraHost)) {
            $allowed[$extraHost] = Resolve-HostnameToIPv4 -Hostname $extraHost
            Write-Log "Allowed hostname pinned: $extraHost -> $($allowed[$extraHost])"
        }
    }

    $blocked = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)

    if (-not $SkipDefaultBlockList) {
        $defaultList = Join-Path $ScriptDir 'default_blocklist.txt'
        foreach ($entry in (Read-BlockListFile -Path $defaultList)) {
            [void]$blocked.Add($entry)
        }
        Write-Log "Loaded default block list from: $defaultList"
    }

    if ($PSBoundParameters.ContainsKey('BlockListPath')) {
        foreach ($entry in (Read-BlockListFile -Path $BlockListPath)) {
            [void]$blocked.Add($entry)
        }
        Write-Log "Loaded custom block list from: $BlockListPath"
    }

    $blocked.Remove($ConsoleHostname) | Out-Null
    foreach ($extra in $AllowedHostnames) {
        [void]$blocked.Remove($extra.Trim().ToLowerInvariant())
    }

    $managedLines = Get-ManagedBlockLines -AllowedEntries $allowed -BlockedHostnames $blocked
    Write-Log "Managed block will contain $($allowed.Count) allowed and $($blocked.Count) blocked hostnames."

    if (-not $Force) {
        Write-Output ''
        Write-Output 'Preview (managed section only):'
        $managedLines | ForEach-Object { Write-Output $_ }
        Write-Output ''
        if (-not (Confirm-Action -Prompt 'Apply hosts file changes?')) {
            Write-Log 'Aborted by operator.'
            return
        }
    }

    $original = Get-Content -LiteralPath $HostsPath
    $existingBlock = Get-ManagedBlockFromContent -ContentLines $original
    if ($existingBlock.Count -eq 0 -and -not (Test-Path -LiteralPath $BackupPath)) {
        Copy-Item -LiteralPath $HostsPath -Destination $BackupPath -Force
        Write-Log "Backup saved: $BackupPath"
    }

    $stripped = Remove-ManagedBlock -ContentLines $original
    $updated = @($stripped + $managedLines)

    if ($PSCmdlet.ShouldProcess($HostsPath, 'Apply Binalyze DNS isolation hosts block')) {
        Set-Content -LiteralPath $HostsPath -Value $updated -Encoding ASCII
        Write-Log "Hosts file updated: $HostsPath"
        Write-Log 'Flush DNS cache...'
        ipconfig /flushdns | Out-Null
        Write-Log 'Done. Verify agent connectivity in the AIR console before leaving the session.'
    }
}

function Disable-DnsIsolation {
    if (-not (Test-Path -LiteralPath $HostsPath)) {
        throw "Hosts file not found: $HostsPath"
    }

    $content = Get-Content -LiteralPath $HostsPath
    $existingBlock = Get-ManagedBlockFromContent -ContentLines $content
    if ($existingBlock.Count -eq 0) {
        Write-Log 'No managed Binalyze DNS isolation block found; nothing to remove.'
        return
    }

    if (-not $Force) {
        if (-not (Confirm-Action -Prompt 'Remove the managed hosts block?')) {
            Write-Log 'Aborted by operator.'
            return
        }
    }

    $stripped = Remove-ManagedBlock -ContentLines $content

    if ($PSCmdlet.ShouldProcess($HostsPath, 'Remove Binalyze DNS isolation hosts block')) {
        Set-Content -LiteralPath $HostsPath -Value $stripped -Encoding ASCII
        Write-Log "Managed block removed from: $HostsPath"
        ipconfig /flushdns | Out-Null
        Write-Log 'DNS cache flushed.'
    }
}

try {
    switch ($Action) {
        'Enable'  { Enable-DnsIsolation }
        'Disable' { Disable-DnsIsolation }
        'Status'  { Show-Status }
    }
}
catch {
    Write-Error $_
    exit 1
}
