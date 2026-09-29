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

$missing = @()
if (-not $uvPath) {
    $missing += "uv"
}
if (-not (Test-PythonVersion -Version $pythonVersion -UvPath $uvPath -CheckCode $versionCheck)) {
    $missing += "Python $pythonVersion"
}

if ($missing.Count -gt 0) {
    if ([Console]::IsInputRedirected) {
        [Console]::Error.WriteLine("Missing {0}. Run this launcher in an interactive terminal to install prerequisites.", ($missing -join ", "))
        exit 2
    }

    $answer = Read-Host ("Missing {0}. Install and continue? [y/N]" -f ($missing -join ", "))
    if ($answer -notmatch '^(?i:y|yes)$') {
        [Console]::Error.WriteLine("Startup cancelled; nothing was installed.")
        exit 1
    }
}

try {
    if (-not $uvPath) {
        New-Item -ItemType Directory -Force -Path $uvDirectory | Out-Null
        $env:UV_INSTALL_DIR = $uvDirectory
        $env:UV_NO_MODIFY_PATH = "1"
        try {
            Invoke-RestMethod -Uri "https://astral.sh/uv/install.ps1" | Invoke-Expression
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
        & $uvPath python install $pythonVersion
        if ($LASTEXITCODE -ne 0) {
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
