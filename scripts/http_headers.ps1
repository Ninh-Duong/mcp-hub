param()

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$serviceConfigPath = Join-Path $repoRoot "config\private\service.json"

try {
    if (-not (Test-Path -LiteralPath $serviceConfigPath -PathType Leaf)) {
        throw "The Hub service configuration is missing."
    }
    $serviceConfig = Get-Content -LiteralPath $serviceConfigPath -Raw | ConvertFrom-Json
    if (-not $serviceConfig.token -or $serviceConfig.host -ne "127.0.0.1" -or $serviceConfig.port -ne 8765) {
        throw "The Hub service configuration is invalid."
    }
    @{ Authorization = "Bearer $($serviceConfig.token)" } | ConvertTo-Json -Compress
} catch {
    [Console]::Error.WriteLine("Could not load the local mcp-hub HTTP header.")
    exit 1
}
