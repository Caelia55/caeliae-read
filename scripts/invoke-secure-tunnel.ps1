[CmdletBinding()]
param(
    [ValidateSet('Doctor', 'Run')]
    [string]$Mode = 'Run',
    [switch]$ReplaceStoredKey,
    [string]$TunnelClientPath
)

$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($TunnelClientPath)) {
    $TunnelClientPath = $env:CAELIAE_TUNNEL_CLIENT
}
if ([string]::IsNullOrWhiteSpace($TunnelClientPath)) {
    throw 'Tunnel client path is not configured. Pass -TunnelClientPath or set CAELIAE_TUNNEL_CLIENT.'
}
$tunnelClient = [Environment]::ExpandEnvironmentVariables($TunnelClientPath)
$profileName = 'coread-core'
$profilePath = Join-Path (Join-Path $env:APPDATA 'tunnel-client') "$profileName.yaml"
$keyDirectory = Join-Path $env:LOCALAPPDATA 'OpenAI\tunnel-client'
$keyPath = Join-Path $keyDirectory 'coread-core-runtime-key.dpapi'

if (-not (Test-Path -LiteralPath $tunnelClient -PathType Leaf)) {
    throw "tunnel-client was not found at the configured path: $tunnelClient"
}
if (-not (Test-Path -LiteralPath $profilePath -PathType Leaf)) {
    throw 'Caeliae Read Tunnel profile is missing. Run configure-secure-tunnel.ps1 first.'
}

if ($Mode -eq 'Run') {
    $portOwner = Get-NetTCPConnection -LocalPort 8081 -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($portOwner) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($portOwner.OwningProcess)"
        if ($process.Name -eq 'tunnel-client.exe' -and $process.CommandLine -match '--profile\s+coread-core') {
            Write-Host 'Caeliae Read Secure MCP Tunnel is already running.'
            exit 0
        }
        throw "Port 8081 is occupied by another process (PID $($portOwner.OwningProcess)); it was not stopped."
    }
}

New-Item -ItemType Directory -Path $keyDirectory -Force | Out-Null
if ($ReplaceStoredKey -or -not (Test-Path -LiteralPath $keyPath -PathType Leaf)) {
    $entered = Read-Host 'Paste the NEW Caeliae Read Runtime API key (input is hidden)' -AsSecureString
    if ($entered.Length -eq 0) {
        throw 'No Runtime API key was entered.'
    }
    $entered | ConvertFrom-SecureString | Set-Content -LiteralPath $keyPath -Encoding UTF8 -NoNewline
    $entered.Dispose()
}

$secureKey = Get-Content -LiteralPath $keyPath -Raw | ConvertTo-SecureString
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
try {
    $env:CONTROL_PLANE_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    $secureKey.Dispose()
}

try {
    if ($Mode -eq 'Doctor') {
        & $tunnelClient doctor --profile $profileName --explain
    }
    else {
        & $tunnelClient run --profile $profileName
    }
    $exitCode = $LASTEXITCODE
}
finally {
    Remove-Item Env:CONTROL_PLANE_API_KEY -ErrorAction SilentlyContinue
}

exit $exitCode
