param(
    [switch]$Service,
    [switch]$Bridge,
    [switch]$Prepare,
    [string]$ClientName
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pythonVersion = (Get-Content -LiteralPath (Join-Path $repoRoot ".python-version") -Raw).Trim()
if ($pythonVersion -notmatch '^\d+\.\d+$') {
    [Console]::Error.WriteLine("Invalid .python-version; expected MAJOR.MINOR.")
    exit 2
}

$versionParts = $pythonVersion.Split(".")
$versionCheck = "import sys; raise SystemExit(sys.version_info[:2] != ($($versionParts[0]), $($versionParts[1])))"
# The uv version installed when none is found, and the SHA-256 of its installer script; update both together.
$uvVersion = "0.12.3"
$uvInstallerSha256 = "7b84813e3fad9586da122e362d4dcba1e2e611664244d004bcfc32b2fdf10430"
$uvDirectory = Join-Path $repoRoot ".tools"
$localUv = Join-Path $uvDirectory "uv.exe"
$uvPath = $null

if (Test-Path -LiteralPath $localUv -PathType Leaf) {
    $uvPath = $localUv
} else {
    $uvCommand = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($uvCommand) {
        $uvPath = $uvCommand.Source
    }
}

function Test-PythonVersion {
    param([string]$Version, [string]$UvPath, [string]$CheckCode)

    if ($UvPath) {
        & $UvPath python find $Version *> $null
        if ($LASTEXITCODE -eq 0) {
            return $true
        }
    }

    $launcher = Get-Command py -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($launcher) {
        & $launcher.Source "-$Version" -c $CheckCode *> $null
        if ($LASTEXITCODE -eq 0) {
            return $true
        }
    }

    foreach ($name in @("python$Version", "python3", "python")) {
        $candidate = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($candidate) {
            & $candidate.Source -c $CheckCode *> $null
            if ($LASTEXITCODE -eq 0) {
                return $true
            }
        }
    }

    return $false
}

try {
    if (-not $uvPath) {
        New-Item -ItemType Directory -Force -Path $uvDirectory | Out-Null
        $env:UV_INSTALL_DIR = $uvDirectory
        $env:UV_NO_MODIFY_PATH = "1"
        $installerPath = Join-Path $uvDirectory "uv-installer.ps1"
        try {
            [Console]::Error.WriteLine("[mcp-hub] Installing uv $uvVersion into the repository-local .tools directory.")
            Invoke-WebRequest -Uri "https://astral.sh/uv/$uvVersion/install.ps1" -OutFile $installerPath -UseBasicParsing
            $installerHash = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($installerHash -ne $uvInstallerSha256) {
                throw "The downloaded uv $uvVersion installer does not match its pinned SHA-256; it was not run."
            }
            $installer = [IO.File]::ReadAllText($installerPath)
            $output = Invoke-Expression $installer 2>&1
            foreach ($line in $output) { [Console]::Error.WriteLine([string]$line) }
        } finally {
            Remove-Item -LiteralPath $installerPath -Force -ErrorAction SilentlyContinue
            Remove-Item Env:UV_INSTALL_DIR -ErrorAction SilentlyContinue
            Remove-Item Env:UV_NO_MODIFY_PATH -ErrorAction SilentlyContinue
        }
        if (-not (Test-Path -LiteralPath $localUv -PathType Leaf)) {
            throw "The uv installer did not create .tools/uv.exe."
        }
        $uvPath = $localUv
    }

    if (-not (Test-PythonVersion -Version $pythonVersion -UvPath $uvPath -CheckCode $versionCheck)) {
        [Console]::Error.WriteLine("[mcp-hub] Installing Python $pythonVersion through uv.")
        $output = & $uvPath python install $pythonVersion 2>&1
        $installExitCode = $LASTEXITCODE
        foreach ($line in $output) { [Console]::Error.WriteLine([string]$line) }
        if ($installExitCode -ne 0) {
            throw "Could not install Python $pythonVersion."
        }
    }
} catch {
    [Console]::Error.WriteLine("Bootstrap failed: {0}" -f $_.Exception.Message)
    exit 1
}

Set-Location -LiteralPath $repoRoot
if ($Prepare) {
    & $uvPath sync --locked --python $pythonVersion
    exit $LASTEXITCODE
}
$previousClientName = [Environment]::GetEnvironmentVariable("MCP_HUB_CLIENT_NAME", "Process")
if ($ClientName -and -not $Bridge) {
    [Console]::Error.WriteLine("-ClientName can only be used with -Bridge.")
    exit 2
}
$entrypoint = if ($Service) { "mcp-hub-service" } elseif ($Bridge) { "mcp-hub-bridge" } else { "mcp-hub" }
if ($Bridge) {
    $clientLabel = if ($ClientName) { $ClientName } else { "unknown" }
    [Environment]::SetEnvironmentVariable("MCP_HUB_CLIENT_NAME", $clientLabel, "Process")
}
try {
    & $uvPath run --locked --python $pythonVersion $entrypoint
    $entrypointExitCode = $LASTEXITCODE
} finally {
    if ($Bridge) {
        [Environment]::SetEnvironmentVariable("MCP_HUB_CLIENT_NAME", $previousClientName, "Process")
    }
}
exit $entrypointExitCode
