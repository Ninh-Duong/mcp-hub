param()

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$serviceConfigPath = Join-Path $repoRoot "config\private\service.json"

try {
    if (-not (Test-Path -LiteralPath $serviceConfigPath -PathType Leaf)) {
        throw "The Hub service configuration is missing."
    }
    $serviceConfig = Get-Content -LiteralPath $serviceConfigPath -Raw | ConvertFrom-Json
    # The port follows settings.http.port; the client's URL must use the same one.
    $port = $serviceConfig.port
    if (-not $serviceConfig.token -or $serviceConfig.host -ne "127.0.0.1" -or $port -isnot [int] -or $port -lt 1024 -or $port -gt 65535) {
        throw "The Hub service configuration is invalid."
    }
    @{ Authorization = "Bearer $($serviceConfig.token)" } | ConvertTo-Json -Compress
} catch {
    [Console]::Error.WriteLine("Could not load the local mcp-hub HTTP header.")
    exit 1
}
