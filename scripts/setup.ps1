# One guided setup for people who do not want to learn the commands: run Setup.cmd (double-click) or
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup.ps1
# It asks plain yes/no questions (Enter keeps the suggested answer) and can be run again at any time:
# every step skips what is already done. Account details are typed only into each child MCP's own setup
# screen or file; this script never reads, prints, or stores them.

param(
    [switch]$Interactive,
    [switch]$Unattended
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$parent = Split-Path $repoRoot -Parent
$hubctl = Join-Path $PSScriptRoot "hubctl.ps1"
$hubPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
# The child MCPs this Hub knows, and what each gives the AI client.
$children = [ordered]@{
    "mcp-jira-sync"    = "Jira tickets"
    "mcp-pr-companion" = "Bitbucket pull requests"
    "mcp-server-logs"  = "Logs from OpenObserve or Seq"
    "mcp-read-source"  = "Reading your project source code"
}

function Write-Step {
    param([int]$Number, [string]$Title)
    Write-Host ""
    Write-Host ("[{0}/7] {1}" -f $Number, $Title) -ForegroundColor Cyan
}

function Read-YesNo {
    param([string]$Question, [bool]$Default = $true)
    if ($Unattended) { return $Default }
    $hint = if ($Default) { "Y/n" } else { "y/N" }
    while ($true) {
        $answer = (Read-Host "$Question [$hint]").Trim().ToLowerInvariant()
        if (-not $answer) { return $Default }
        if ($answer -in @("y", "yes")) { return $true }
        if ($answer -in @("n", "no")) { return $false }
        Write-Host "Please answer y or n."
    }
}

function Invoke-Hubctl {
    param([string[]]$Arguments)
    & $powershell -NoProfile -ExecutionPolicy Bypass -File $hubctl @Arguments
    return $LASTEXITCODE -eq 0
}

function Update-SessionPath {
    # Node.js or Git installed a moment ago by winget is on the machine PATH, not yet in this window's.
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}

function Select-Children {
    param([bool]$DefaultAll = $false)
    $chosen = @()
    foreach ($name in $children.Keys) {
        $present = Test-Path -LiteralPath (Join-Path $parent $name) -PathType Container
        $suggest = if ($DefaultAll) { $true } else { $present }
        if (Read-YesNo ("Use {0} ({1})?" -f $children[$name], $name) $suggest) { $chosen += $name }
    }
    return $chosen
}

function Get-RepositoryBase {
    # The child repositories live next to this one on the same host, so their address comes from this clone's.
    $remote = ""
    try { $remote = [string](& git -C $repoRoot remote get-url origin 2>$null) } catch { $remote = "" }
    if ($remote -match '^(.*)/mcp-hub(?:\.git)?/?$') { return $Matches[1] }
    return (Read-Host "Paste the address of the account that hosts mcp-hub (for example https://github.com/<owner>)").TrimEnd("/")
}

function Assert-Git {
    if (Get-Command git -CommandType Application -ErrorAction SilentlyContinue) { return }
    Write-Host "Installing Git (needed to download the child MCPs) ..."
    & winget install --id Git.Git -e --silent --accept-package-agreements --accept-source-agreements
    Update-SessionPath
    if (-not (Get-Command git -CommandType Application -ErrorAction SilentlyContinue)) {
        throw "Git could not be installed. Install it from https://git-scm.com and run Setup again."
    }
}

function Assert-Node {
    if (Get-Command node -CommandType Application -ErrorAction SilentlyContinue) { return }
    Write-Host "Installing Node.js LTS (needed for child MCPs) ..."
    & winget install --id OpenJS.NodeJS.LTS -e --silent --accept-package-agreements --accept-source-agreements
    Update-SessionPath
}

function Get-ChildRepositories {
    param([string[]]$Chosen)
    $missing = @($Chosen | Where-Object { -not (Test-Path -LiteralPath (Join-Path $parent $_) -PathType Container) })
    if (-not $missing.Count) { Write-Host "All chosen child MCPs are already next to mcp-hub."; return }
    Assert-Git
    Assert-Node
    $base = Get-RepositoryBase
    foreach ($name in $missing) {
        Write-Host "Downloading $name ..."
        & git -C $parent clone --quiet "$base/$name.git"
        if ($LASTEXITCODE -ne 0) { Write-Warning "Could not download $name; set it up later by running Setup again." }
    }
}

function Install-StatusApp {
    $installed = Get-ItemProperty -Path "HKCU:\Software\mcp-hub-status" -Name InstallPath -ErrorAction SilentlyContinue
    if ($installed) { Write-Host "The mcp-hub Status app is already installed."; return }
    if (-not (Read-YesNo "Install the mcp-hub Status app (a small window that shows and starts the Hub)?")) { return }
    if (-not (Invoke-Hubctl @("app", "-Install"))) {
        Write-Warning "The app was not installed. You can download it later from the repository's Releases page."
    }
}

function Set-JiraAccount {
    Write-Host "The Jira setup asks for your Jira address, email, and API token. After it says you are logged in, choose Exit."
    Push-Location (Join-Path $parent "mcp-jira-sync")
    try { & $hubPython cli.py } finally { Pop-Location }
}

function Set-BitbucketAccount {
    Write-Host "The Bitbucket setup opens its menu: log in, then choose Exit."
    Push-Location (Join-Path $parent "mcp-pr-companion")
    try { & node bin/cli.js } finally { Pop-Location }
}

function Set-LogsAccount {
    $config = Join-Path $parent "mcp-server-logs\config"
    if (Read-YesNo "Do you use OpenObserve for logs? (answer n for Seq)") {
        foreach ($stage in @("dev", "stg", "prod")) {
            if (-not (Read-YesNo "Fill in the OpenObserve account for $stage?" ($stage -eq "dev"))) { continue }
            $file = Join-Path $config "openobserve.$stage.env"
            if (-not (Test-Path -LiteralPath $file)) { Copy-Item -LiteralPath (Join-Path $config "openobserve.env.example") $file }
            Write-Host "Notepad opens the $stage file: fill in OPENOBSERVE_URL, OPENOBSERVE_EMAIL, and OPENOBSERVE_TOKEN, save, and close it."
            Start-Process notepad.exe -ArgumentList "`"$file`"" -Wait
        }
        return
    }
    Write-Host "The logs setup opens its menu: choose 1. Seq, enter the connection, then exit."
    Push-Location (Join-Path $parent "mcp-server-logs")
    try { & node start.mjs --cli } finally { Pop-Location }
}

function Select-Folder {
    param([string]$Title)
    try {
        Add-Type -AssemblyName System.Windows.Forms
        $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
        $dialog.Description = $Title
        if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { return $dialog.SelectedPath }
        return $null
    } catch {
        return (Read-Host "$Title (type the full folder path)")
    }
}

function Set-SourceProjects {
    # mcp-read-source reads only folders registered here, by a short name the AI client uses.
    $file = Join-Path $parent "mcp-read-source\config\private\projects.json"
    $projects = [ordered]@{}
    if (Test-Path -LiteralPath $file) {
        $saved = Get-Content -LiteralPath $file -Raw | ConvertFrom-Json
        foreach ($entry in $saved.projects.PSObject.Properties) { $projects[$entry.Name] = [string]$entry.Value }
    }
    Write-Host ("Projects already registered: {0}" -f $(if ($projects.Count) { ($projects.Keys -join ", ") } else { "none" }))
    while (Read-YesNo "Add a project folder the AI client may read?" (-not $projects.Count)) {
        $alias = (Read-Host "Short name for it (letters, digits, - or _; for example orders-api)").Trim()
        if ($alias -notmatch '^[A-Za-z0-9_-]{1,64}$') { Write-Host "Use only letters, digits, - or _."; continue }
        $folder = Select-Folder "Choose the folder of $alias"
        if (-not $folder -or -not (Test-Path -LiteralPath $folder -PathType Container)) { Write-Host "No folder chosen."; continue }
        $projects[$alias] = $folder.Replace("\", "/")
    }
    New-Item -ItemType Directory -Force -Path (Split-Path $file) | Out-Null
    $json = [ordered]@{ version = 1; projects = $projects } | ConvertTo-Json -Depth 3
    [IO.File]::WriteAllText($file, $json + "`n", (New-Object Text.UTF8Encoding($false)))
}

function Initialize-ChildTemplates {
    param([string[]]$Chosen)
    if ($Chosen -contains "mcp-server-logs") {
        $logsConfig = Join-Path $parent "mcp-server-logs\config"
        if (Test-Path -LiteralPath $logsConfig -PathType Container) {
            $example = Join-Path $logsConfig "openobserve.env.example"
            $target = Join-Path $logsConfig "openobserve.dev.env"
            if ((Test-Path -LiteralPath $example) -and -not (Test-Path -LiteralPath $target)) {
                Copy-Item -LiteralPath $example $target
            }
        }
    }
    if ($Chosen -contains "mcp-read-source") {
        $sourceConfig = Join-Path $parent "mcp-read-source\config\private\projects.json"
        if (-not (Test-Path -LiteralPath $sourceConfig)) {
            New-Item -ItemType Directory -Force -Path (Split-Path $sourceConfig) | Out-Null
            [IO.File]::WriteAllText($sourceConfig, "{`"version`": 1, `"projects`": {}}`n", (New-Object Text.UTF8Encoding($false)))
        }
    }
}

function Set-Accounts {
    param([string[]]$Chosen)
    Update-SessionPath
    Initialize-ChildTemplates -Chosen $Chosen
    $steps = [ordered]@{
        "mcp-jira-sync" = { Set-JiraAccount }; "mcp-pr-companion" = { Set-BitbucketAccount }
        "mcp-server-logs" = { Set-LogsAccount }; "mcp-read-source" = { Set-SourceProjects }
    }
    foreach ($name in $Chosen) {
        if (-not (Test-Path -LiteralPath (Join-Path $parent $name))) { continue }
        if (-not (Read-YesNo ("Set up {0} now? (answer n to configure later in its repository)" -f $children[$name]) $false)) { continue }
        try { & $steps[$name] } catch { Write-Warning ("{0} setup stopped: {1}. Run Setup again to retry." -f $name, $_.Exception.Message) }
    }
}

function Show-Check {
    Push-Location $repoRoot
    try { & $hubPython scripts\smoke.py } finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "A child marked FAIL needs its account configured in its repository. See docs/parent-child-architecture.md." -ForegroundColor Yellow
        Write-Host "The Hub and status app are running; configure missing credentials when ready."
    }
}

function Copy-ClientPrompt {
    # The AI client connects itself: docs/connecting-a-client.md, prompt A, with this clone's path filled in.
    $path = $repoRoot.Replace("\", "/")
    $prompt = @"
Read $path/docs/connecting-a-client.md and follow every step to connect yourself to mcp-hub,
using the file for this client in $path/docs/clients/: register one MCP server named mcp-hub,
use the shared Windows Hub over authenticated HTTP when this client supports the documented local header helper,
or use its stdio bridge; replace any old entries for its child MCPs, install the always-on rule and the /mcp-hub command.
Ask me before writing global config or removing entries, and report what you changed at the end.
"@
    try { Set-Clipboard -Value $prompt; $copied = $true } catch { $copied = $false }
    Write-Host "Open your AI client (Claude Code, Codex, Antigravity, Cursor, VS Code, Gemini CLI) and paste this message into it:"
    if (-not $copied) { Write-Host $prompt }
    else { Write-Host "(It is already copied: just paste it with Ctrl+V.)" -ForegroundColor Green }
    Write-Host "The AI client sets itself up and tells you when to restart it. Then try: /mcp-hub mcp-jira-sync check connection"
}

function Invoke-Setup {
    Write-Host "mcp-hub setup. Press Enter to keep the suggested answer; run this again any time to change something." -ForegroundColor Green
    Write-Step 1 "Choose what the AI client may use"
    $chosen = @(Select-Children -DefaultAll:(-not $Interactive))
    Write-Step 2 "Download the chosen child MCPs"
    Get-ChildRepositories -Chosen $chosen
    Write-Step 3 "Install the status app"
    Install-StatusApp
    Write-Step 4 "Install and start the Hub (it installs Python, Node.js, and what each child needs; Windows may ask for permission)"
    if (-not (Invoke-Hubctl @("install"))) { throw "The Hub could not be installed. Run Setup again, or see docs/operations/shared-agent-hub.md." }
    Write-Step 5 "Connect your accounts (typed only into each tool's own screen, never into the AI chat)"
    Set-Accounts -Chosen $chosen
    [void](Invoke-Hubctl @("restart"))
    Write-Step 6 "Check every child MCP"
    Show-Check
    Write-Step 7 "Connect your AI client"
    Copy-ClientPrompt
    Write-Host ""
    Write-Host "Done. The Hub starts by itself at every sign-in." -ForegroundColor Green
}

# Dot-sourcing (`. .\scripts\setup.ps1`, as the tests do) loads the steps without running them.
if ($MyInvocation.InvocationName -ne ".") { Invoke-Setup }
