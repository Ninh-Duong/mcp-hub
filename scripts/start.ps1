$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pythonVersion = (Get-Content -LiteralPath (Join-Path $repoRoot ".python-version") -Raw).Trim()
if ($pythonVersion -notmatch '^\d+\.\d+$') {
    [Console]::Error.WriteLine("Invalid .python-version; expected MAJOR.MINOR.")
    exit 2
}

$versionParts = $pythonVersion.Split(".")
$versionCheck = "import sys; raise SystemExit(sys.version_info[:2] != ($($versionParts[0]), $($versionParts[1])))"
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
        try {
            [Console]::Error.WriteLine("[mcp-hub] Installing uv into the repository-local .tools directory.")
            $installer = Invoke-RestMethod -Uri "https://astral.sh/uv/install.ps1"
            $output = Invoke-Expression $installer 2>&1
            foreach ($line in $output) { [Console]::Error.WriteLine([string]$line) }
        } finally {
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
& $uvPath run --locked --python $pythonVersion mcp-hub
exit $LASTEXITCODE
