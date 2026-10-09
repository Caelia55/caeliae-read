[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^tunnel_[A-Za-z0-9]+$')]
    [string]$TunnelId,
    [string]$TunnelClientPath
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($TunnelClientPath)) {
    $TunnelClientPath = $env:CAELIAE_TUNNEL_CLIENT
}
if ([string]::IsNullOrWhiteSpace($TunnelClientPath)) {
    throw 'Tunnel client path is not configured. Pass -TunnelClientPath or set CAELIAE_TUNNEL_CLIENT.'
}
$tunnelClient = [Environment]::ExpandEnvironmentVariables($TunnelClientPath)
$profileName = 'coread-core'
$profileRoot = Join-Path $env:APPDATA 'tunnel-client'
$profilePath = Join-Path $profileRoot "$profileName.yaml"
$icloudProfile = Join-Path $profileRoot 'icloud-calendar.yaml'
$mcpLauncher = Join-Path $projectRoot 'start-caeliae-read-mcp.cmd'

if (-not (Test-Path -LiteralPath $tunnelClient -PathType Leaf)) {
    throw "tunnel-client was not found at the configured path: $tunnelClient"
}
if (-not (Test-Path -LiteralPath $mcpLauncher -PathType Leaf)) {
    throw 'Caeliae Read MCP launcher is missing.'
}
if (Test-Path -LiteralPath $profilePath) {
    throw "The independent Caeliae Read profile already exists: $profilePath"
}

$portOwner = Get-NetTCPConnection -LocalPort 8081 -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($portOwner) {
    throw "Caeliae Read Tunnel admin port 8081 is already occupied by PID $($portOwner.OwningProcess)."
}

$mcpCommandPath = $mcpLauncher.Replace('\', '/')
& $tunnelClient init `
    --sample sample_mcp_stdio_local `
    --profile $profileName `
    --tunnel-id $TunnelId `
    --mcp-command "cmd.exe /d /c $mcpCommandPath" `
    --health-listen-addr '127.0.0.1:8081'
if ($LASTEXITCODE -ne 0) {
    throw "tunnel-client init failed with exit code $LASTEXITCODE."
}

if (-not (Test-Path -LiteralPath $profilePath -PathType Leaf)) {
    throw 'tunnel-client did not create the expected Caeliae Read profile.'
}
if (-not (Test-Path -LiteralPath $icloudProfile -PathType Leaf)) {
    Write-Warning 'The existing iCloud profile was not found; no iCloud profile was created or modified.'
}

Write-Host 'Created independent Secure MCP Tunnel profile: coread-core'
Write-Host 'Local admin UI: http://127.0.0.1:8081/ui'
Write-Host 'The Runtime API key is not stored in this profile.'
