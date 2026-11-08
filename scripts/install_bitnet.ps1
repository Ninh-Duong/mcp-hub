[CmdletBinding()]
param(
    [string]$Model = "BitNet-b1.58-2B-4T"
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path "$PSScriptRoot\..").Path

function Fail-Bitnet([string]$Step, [string]$Reason) {
    Write-Output "bitnet: unavailable ($Step: $Reason)"
    exit 2
}

$InstallJsonPath = Join-Path $RepoRoot "vendor\bitnet\install.json"
if (Test-Path $InstallJsonPath) {
    try {
        $info = Get-Content $InstallJsonPath -Raw -Encoding utf8 | ConvertFrom-Json
        $serverPath = Join-Path $RepoRoot $info.server
        $modelPath = Join-Path $RepoRoot $info.model
        if ((Test-Path $serverPath) -and (Test-Path $modelPath)) {
            Write-Output "bitnet: ready"
            exit 0
        }
    } catch {}
}

# 1. Requirements: git
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Fail-Bitnet "requirements" "git not found on PATH"
}

# 2. Requirements: cmake >= 3.22
$cmakeOk = $false
$cmakeCmd = Get-Command cmake -ErrorAction SilentlyContinue
if ($cmakeCmd) {
    try {
        $cmakeVer = & cmake --version
        if ($cmakeVer -match 'cmake version (\d+)\.(\d+)') {
            $major = [int]$Matches[1]
            $minor = [int]$Matches[2]
            if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 22)) {
                $cmakeOk = $true
            }
        }
    } catch {}
}
if (-not $cmakeOk) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        try {
            winget install --id Kitware.CMake -e --silent --accept-package-agreements --accept-source-agreements
            $cmakeCmd = Get-Command cmake -ErrorAction SilentlyContinue
            if ($cmakeCmd) { $cmakeOk = $true }
        } catch {}
    }
}
if (-not $cmakeOk) {
    Fail-Bitnet "requirements" "cmake >= 3.22 required"
}

# 3. Requirements: clang >= 18 or VS 2022 ClangCL
$compilerOk = $false
$clangCmd = Get-Command clang -ErrorAction SilentlyContinue
if ($clangCmd) {
    try {
        $clangVer = & clang --version
        if ($clangVer -match 'clang version (\d+)') {
            if ([int]$Matches[1] -ge 18) { $compilerOk = $true }
        }
    } catch {}
}
if (-not $compilerOk) {
    $vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
    if (Test-Path $vswhere) {
        $vsClang = & $vswhere -products * -requires Microsoft.VisualStudio.Component.VC.Llvm.Clang
        if ($vsClang) { $compilerOk = $true }
    }
}
if (-not $compilerOk) {
    $vsInstallCmd = 'winget install Microsoft.VisualStudio.2022.BuildTools --override "--add Microsoft.VisualStudio.Workload.VCTools --add Microsoft.VisualStudio.Component.VC.Llvm.Clang --add Microsoft.VisualStudio.Component.VC.Llvm.ClangToolset --passive"'
    Write-Host $vsInstallCmd
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        try {
            Invoke-Expression $vsInstallCmd
            $vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
            if (Test-Path $vswhere) {
                $vsClang = & $vswhere -products * -requires Microsoft.VisualStudio.Component.VC.Llvm.Clang
                if ($vsClang) { $compilerOk = $true }
            }
        } catch {}
    }
}
if (-not $compilerOk) {
    Fail-Bitnet "requirements" "clang >= 18 or VS 2022 ClangCL required"
}

# 4. Clone repo
$BitnetDir = Join-Path $RepoRoot "vendor\bitnet"
if (-not (Test-Path $BitnetDir)) {
    New-Item -ItemType Directory -Path $BitnetDir -Force | Out-Null
}
$SrcDir = Join-Path $BitnetDir "src"
if (-not (Test-Path $SrcDir)) {
    git clone --recursive --depth 1 https://github.com/microsoft/BitNet $SrcDir
    if ($LASTEXITCODE -ne 0) {
        Fail-Bitnet "clone" "git clone failed"
    }
}

# 5. Virtualenv + dependencies
$VenvDir = Join-Path $BitnetDir "venv"
if (-not (Test-Path $VenvDir)) {
    uv venv $VenvDir --python 3.11
    if ($LASTEXITCODE -ne 0) {
        Fail-Bitnet "venv" "uv venv failed"
    }
}
$ReqFile = Join-Path $SrcDir "requirements.txt"
if (Test-Path $ReqFile) {
    uv pip install --python $VenvDir -r $ReqFile
    if ($LASTEXITCODE -ne 0) {
        Fail-Bitnet "pip" "requirements install failed"
    }
}

# 6. Model download
$HfCli = Join-Path $VenvDir "Scripts\huggingface-cli.exe"
$ModelDir = Join-Path $BitnetDir "models\$Model"
if (-not (Test-Path $ModelDir)) {
    New-Item -ItemType Directory -Path $ModelDir -Force | Out-Null
}
if (Test-Path $HfCli) {
    & $HfCli download microsoft/BitNet-b1.58-2B-4T-gguf --local-dir $ModelDir
    if ($LASTEXITCODE -ne 0) {
        Fail-Bitnet "model" "download failed"
    }
}

# 7. Build
$PythonExe = Join-Path $VenvDir "Scripts\python.exe"
$SetupEnv = Join-Path $SrcDir "setup_env.py"
if ((Test-Path $PythonExe) -and (Test-Path $SetupEnv)) {
    Push-Location $SrcDir
    try {
        & $PythonExe setup_env.py -md $ModelDir -q i2_s
        $buildCode = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($buildCode -ne 0) {
        Fail-Bitnet "build" "setup_env.py build failed"
    }
}

# 8. Locate binary and model, write install.json
$BuildDir = Join-Path $SrcDir "build"
$serverItem = Get-ChildItem -Path $BuildDir -Recurse -Filter llama-server.exe -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $serverItem) {
    Fail-Bitnet "build" "llama-server.exe not found under build directory"
}
$modelItem = Get-ChildItem -Path $ModelDir -Recurse -Filter "*i2_s*.gguf" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $modelItem) {
    $modelItem = Get-ChildItem -Path $ModelDir -Recurse -Filter "*.gguf" -ErrorAction SilentlyContinue | Select-Object -First 1
}
if (-not $modelItem) {
    Fail-Bitnet "model" "gguf model file not found"
}

$relServer = $serverItem.FullName.Substring($RepoRoot.Length).TrimStart('\', '/').Replace('\', '/')
$relModel = $modelItem.FullName.Substring($RepoRoot.Length).TrimStart('\', '/').Replace('\', '/')
$manifest = @{
    server = $relServer
    model = $relModel
    version = 1
} | ConvertTo-Json -Compress

Set-Content -Path $InstallJsonPath -Value $manifest -Encoding utf8
Write-Output "bitnet: ready"
exit 0
