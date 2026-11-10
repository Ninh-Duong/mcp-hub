# One unattended setup: run Setup.cmd (double-click) or
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup.ps1
# It asks nothing and can be run again at any time: every step skips what is already done. It downloads the default
# child MCPs listed in config\hub.example.json, installs the Hub, checks every child in turn (a failing child never
# stops the rest), checks the Hub, and copies a message for the AI client. Account values are never typed, read,
# printed, or stored here: the user fills them in each child MCP's own repository (docs/parent-child-architecture.md).

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$parent = Split-Path $repoRoot -Parent
$hubctl = Join-Path $PSScriptRoot "hubctl.ps1"
$hubPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
# What each child needs from the user after setup. Descriptions only: nothing here reads or writes a value.
$accountSteps = [ordered]@{
    "mcp-jira-sync"    = "run: uv run python cli.py (Jira address, email, API token)"
    "mcp-pr-companion" = "copy .env.example to .env and fill BITBUCKET_EMAIL, BITBUCKET_API_TOKEN, BITBUCKET_REPOSITORY"
    "mcp-server-logs"  = "edit config/openobserve.<dev|stg|prod>.env, or run: node start.mjs --cli (Seq)"
    "mcp-read-source"  = "add your project folders to config/private/projects.json"
    "mcp-sqlserver"    = "copy dbconfig.example.json to dbconfig.json and fill server, username, password (a read-only login is recommended: docs/readonly-login.sql)"
}
# AI clients that docs/clients/ covers: a command on PATH or a folder in the user profile shows it is installed.
$aiClients = [ordered]@{
    "Claude Code"       = @{ Commands = @("claude"); Folders = @(".claude") }
    "Codex"             = @{ Commands = @("codex"); Folders = @(".codex") }
    "Antigravity"       = @{ Commands = @("antigravity"); Folders = @(".gemini\antigravity", ".gemini\config") }
    "Cursor"            = @{ Commands = @("cursor"); Folders = @(".cursor") }
    "VS Code (Copilot)" = @{ Commands = @("code"); Folders = @("AppData\Roaming\Code\User") }
    "Gemini CLI"        = @{ Commands = @("gemini"); Folders = @(".gemini\settings.json") }
}

function Write-Step {
    param([int]$Number, [string]$Title)
    Write-Host ""
    Write-Host ("[{0}/6] {1}" -f $Number, $Title) -ForegroundColor Cyan
}

function Invoke-Hubctl {
    param([string[]]$Arguments)
    & $powershell -NoProfile -ExecutionPolicy Bypass -File $hubctl @Arguments | Out-Host
    return $LASTEXITCODE -eq 0
}

function Update-SessionPath {
    # Node.js or Git installed a moment ago by winget is on the machine PATH, not yet in this window's.
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}

function Test-Command {
    param([string]$Name)
    return [bool](Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue)
}

function Get-DefaultChildren {
    # id -> repository address, the one list of default children (config\hub.example.json).
    $servers = (Get-Content -LiteralPath (Join-Path $repoRoot "config\hub.example.json") -Raw | ConvertFrom-Json).servers
    $children = [ordered]@{}
    foreach ($entry in $servers.PSObject.Properties) { $children[$entry.Name] = [string]$entry.Value.repository }
    return $children
}

function Assert-Git {
    if (Test-Command git) { return }
    Write-Host "Installing Git (needed to download the child MCPs) ..."
    & winget install --id Git.Git -e --silent --accept-package-agreements --accept-source-agreements | Out-Host
    Update-SessionPath
    if (-not (Test-Command git)) { throw "Git could not be installed. Install it from https://git-scm.com and run Setup again." }
}

function Get-ChildRepositories {
    # Clones each default child that is not already next to this repository. Returns the ones that could not be cloned.
    $children = Get-DefaultChildren
    $missing = @($children.Keys | Where-Object { -not (Test-Path -LiteralPath (Join-Path $parent $_) -PathType Container) })
    if (-not $missing.Count) { Write-Host "All default child MCPs are already next to mcp-hub."; return @() }
    Assert-Git
    $failed = @()
    foreach ($name in $missing) {
        Write-Host "Downloading $name ..."
        & git -C $parent clone --quiet $children[$name] | Out-Host
        if ($LASTEXITCODE -ne 0) { $failed += $name; Write-Warning "Could not download $name; run Setup again later." }
    }
    return $failed
}

function Install-StatusApp {
    if (-not (Invoke-Hubctl @("app", "-Install"))) {
        Write-Warning "The Status app could not be installed or refreshed. Setup will continue; try the repository's Releases page later."
    }
}

function Show-Check {
    # Children one after another, then the Hub (scripts/smoke.py). Returns its exit code: 1 only when something FAILED.
    Push-Location $repoRoot
    try { & $hubPython scripts\smoke.py | Out-Host } finally { Pop-Location }
    return $LASTEXITCODE
}

function Find-AiClients {
    $found = @()
    foreach ($name in $aiClients.Keys) {
        $client = $aiClients[$name]
        $hasCommand = @($client.Commands | Where-Object { Test-Command $_ }).Count -gt 0
        $hasFolder = @($client.Folders | Where-Object { Test-Path -LiteralPath (Join-Path $env:USERPROFILE $_) }).Count -gt 0
        if ($hasCommand -or $hasFolder) { $found += $name }
    }
    return $found
}

function Show-AccountNextSteps {
    param([string[]]$Cloned)
    Write-Host "Accounts are filled in each child's own repository, by you only (never through Setup or an AI chat):"
    foreach ($name in $accountSteps.Keys) {
        if ($Cloned -contains $name) { Write-Host ("  ../{0}: {1}" -f $name, $accountSteps[$name]) }
    }
    Write-Host "Then run: scripts\hubctl.ps1 restart, and uv run python scripts/smoke.py to check (docs/parent-child-architecture.md)."
}

function Copy-ClientPrompt {
    param([string[]]$Clients)
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
    if ($Clients.Count) { Write-Host ("AI clients found on this PC: {0}." -f ($Clients -join ", ")) }
    else { Write-Host "No AI client was found on this PC. Install one (Claude Code, Codex, Antigravity, Cursor, VS Code, Gemini CLI), then paste the message below." }
    Write-Host "Open each AI client you want to use and paste this message into it:"
    if ($copied) { Write-Host "(It is already copied: just paste it with Ctrl+V.)" -ForegroundColor Green } else { Write-Host $prompt }
    Write-Host "The AI client sets itself up and tells you when to restart it. Then try: /mcp-hub mcp-jira-sync check connection"
}

function Invoke-Setup {
    Write-Host "mcp-hub setup. It asks nothing; run it again any time." -ForegroundColor Green
    Write-Step 1 "Download the default child MCPs that are missing"
    $failed = @(Get-ChildRepositories)
    Write-Step 2 "Install or refresh the status app"
    Install-StatusApp
    Write-Step 3 "Install and start the Hub (it installs Python, Node.js, and what each child needs; Windows may ask for permission)"
    if (-not (Invoke-Hubctl @("install"))) { throw "The Hub could not be installed. Run Setup again, or see docs/operations/shared-agent-hub.md." }
    Write-Step 4 "Check every child MCP, then the Hub (a missing account is only a warning)"
    $checkExit = Show-Check
    Write-Step 5 "Connect your AI clients"
    Copy-ClientPrompt -Clients @(Find-AiClients)
    Write-Step 6 "Your accounts"
    $present = @((Get-DefaultChildren).Keys | Where-Object { Test-Path -LiteralPath (Join-Path $parent $_) -PathType Container })
    Show-AccountNextSteps -Cloned $present
    Write-Host ""
    if ($failed.Count) { Write-Warning ("Not downloaded: {0}." -f ($failed -join ", ")) }
    if ($checkExit -ne 0 -or $failed.Count) { Write-Host "Done, with problems: see FAIL above." -ForegroundColor Yellow; return 1 }
    Write-Host "Done. The Hub starts by itself at every sign-in." -ForegroundColor Green
    return 0
}

# Dot-sourcing (`. .\scripts\setup.ps1`, as the tests do) loads the steps without running them.
if ($MyInvocation.InvocationName -ne ".") { $script:setupExit = Invoke-Setup | Select-Object -Last 1; exit $script:setupExit }
