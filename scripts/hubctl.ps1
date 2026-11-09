param(
    [Parameter(Position = 0)]
    [ValidateSet("install", "status", "start", "stop", "restart", "open", "preflight", "app", "autostart")]
    [string]$Command = "status",
    # start, restart, open, install: run the activity UI (barn, daily activity, connected clients) for this start,
    # even when the saved mode (desktop app: Settings > Hub > Track AI clients) is fast.
    [switch]$Ui,
    # autostart: switch the Hub's logon task on or off. Without -Target it prints the current state.
    # The desktop status app is installed on its own (mcp-hub-status-setup-<version>.exe) and manages its own sign-in.
    [ValidateSet("hub")]
    [string]$Target,
    [ValidateSet("on", "off")]
    [string]$State,
    # app: download the desktop app's latest release, check it against its SHA-256, and run its setup.
    [switch]$Install
)

$ErrorActionPreference = "Stop"
$taskName = "mcp-hub"
# Registered by older versions to open the desktop app at logon; install removes it (the app's installer owns that now).
$statusAppTaskName = "mcp-hub-status"
# Written by the desktop app's installer: where mcp-hub-status.exe lives.
$statusAppKey = "HKCU:\Software\mcp-hub-status"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$startScript = Join-Path $PSScriptRoot "start.ps1"
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$launchPath = Join-Path $repoRoot "config\private\launch.json"
$autostartTasks = [ordered]@{ hub = $taskName }

function Get-HubUrl {
    # The Hub writes its URL (127.0.0.1 and settings.http.port) to the connection file at every start;
    # before the first start there is none, and the default port applies.
    $connectionPath = Get-ConnectionPath
    if (Test-Path -LiteralPath $connectionPath -PathType Leaf) {
        $connection = Get-Content -LiteralPath $connectionPath -Raw | ConvertFrom-Json
        if ([string]$connection.url -match '^http://127\.0\.0\.1:\d{4,5}$') { return [string]$connection.url }
    }
    return "http://127.0.0.1:8765"
}

function Test-HubHealth {
    try {
        $response = Invoke-RestMethod -Uri "$(Get-HubUrl)/health" -Method Get -TimeoutSec 2
        return $response.ok -eq $true
    } catch {
        return $false
    }
}

function Get-ConnectionPath {
    # The same lookup as service_config.connection_file().
    if ($env:MCP_HUB_CONNECTION_FILE) { return $env:MCP_HUB_CONNECTION_FILE }
    if ($env:MCP_HUB_PRIVATE_DIR) { return Join-Path $env:MCP_HUB_PRIVATE_DIR "connection.json" }
    return Join-Path $env:LOCALAPPDATA "mcp-hub\connection.json"
}

function Get-AdminToken {
    # The dashboard API takes only the admin token, which the Hub makes new at every start and writes only to
    # this user's connection file; the client token in service.json reaches the MCP endpoint alone.
    $connectionPath = Get-ConnectionPath
    if (-not (Test-Path -LiteralPath $connectionPath -PathType Leaf)) {
        throw "The Hub's connection file is missing; start the Hub first."
    }
    $connection = Get-Content -LiteralPath $connectionPath -Raw | ConvertFrom-Json
    if (-not $connection.token) { throw "The Hub's connection file is invalid; restart the Hub." }
    return [string]$connection.token
}

function Invoke-Hidden {
    param([string]$FilePath, [string]$Arguments)

    # No console at all: Windows Terminal as default terminal ignores -WindowStyle Hidden.
    $startInfo = [Diagnostics.ProcessStartInfo]::new($FilePath, $Arguments)
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.WorkingDirectory = $repoRoot
    $process = [Diagnostics.Process]::Start($startInfo)
    $output = $process.StandardOutput.ReadToEnd()
    $process.WaitForExit()
    return [pscustomobject]@{ ExitCode = $process.ExitCode; Output = $output }
}

function Sync-HubEnvironment {
    # Absolute path: never a powershell.exe found in the working directory or earlier on PATH.
    $powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    $arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -Prepare' -f $startScript
    $result = Invoke-Hidden -FilePath $powershell -Arguments $arguments
    if ($result.ExitCode -ne 0) { throw "Could not prepare the locked Hub environment (exit code $($result.ExitCode))." }
}

function Invoke-Preflight {
    param([switch]$Install)

    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) { throw "The Hub environment is missing. Run: hubctl start" }
    $arguments = "-m mcp_hub.preflight" + $(if ($Install) { " --install" } else { "" })
    $result = Invoke-Hidden -FilePath $venvPython -Arguments $arguments
    if ($result.Output) { Write-Host $result.Output.TrimEnd() }
    return $result.ExitCode
}

function Stop-StatusApp {
    # The desktop status app keeps Qt libraries in .venv open, so uv sync could not replace them while it runs.
    # It is stateless: stop this checkout's copy (the venv launcher and the interpreter it started).
    $venv = Join-Path $repoRoot ".venv"
    $all = @(Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'")
    $launchers = @($all | Where-Object { $_.CommandLine -and $_.CommandLine.Contains($venv) -and $_.CommandLine.Contains("-m mcp_hub.desktop") })
    $launcherIds = @($launchers | ForEach-Object ProcessId)
    $ids = $launcherIds + @($all | Where-Object { $launcherIds -contains $_.ParentProcessId } | ForEach-Object ProcessId)
    foreach ($id in $ids) { Stop-Process -Id $id -ErrorAction SilentlyContinue }
    return $launcherIds.Count -gt 0
}

function Restore-StatusApp {
    # Only a copy run from this checkout is stopped for a sync; the installed app never uses .venv.
    if ($script:statusAppWasRunning) { Start-SourceStatusApp }
    $script:statusAppWasRunning = $false
}

function Test-HubEnvironment {
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) { return $false }
    return (Invoke-Hidden -FilePath $venvPython -Arguments '-c "import mcp_hub.service"').ExitCode -eq 0
}

function Stop-VenvProcesses {
    # Stop processes that hold files in this checkout's .venv open so uv sync can update them.
    $venv = Join-Path $repoRoot ".venv"
    $all = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.CommandLine -and $_.CommandLine.Contains($venv)
    })
    foreach ($proc in $all) {
        if ($proc.ProcessId -ne $PID) {
            Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
        }
    }
}

function Initialize-HubEnvironment {
    $script:statusAppWasRunning = Stop-StatusApp
    Stop-VenvProcesses
    try {
        Sync-HubEnvironment
    } catch {
        # A running AI-client bridge keeps .venv\Scripts\mcp-hub-bridge.exe open, so uv cannot replace it. The project
        # is installed editable, so the current environment still runs this code: start it instead of leaving the Hub off.
        if (-not (Test-HubEnvironment)) { throw }
        Write-Warning "The Hub environment was not updated because its files are in use; starting with the current one. Close the AI clients, then run: hubctl restart"
    }
    if ((Invoke-Preflight -Install) -ne 0) { Write-Warning "Some child MCPs are not ready; the Hub starts without them. Run: hubctl preflight" }
}

function Set-LaunchMode {
    param([switch]$Ui)

    # The service reads and deletes this file at start, so the next start without -Ui uses the saved mode again.
    if ($Ui) {
        New-Item -ItemType Directory -Force -Path (Split-Path $launchPath) | Out-Null
        [IO.File]::WriteAllText($launchPath, '{"extensions": ["activity-ui"]}')
    } elseif (Test-Path -LiteralPath $launchPath -PathType Leaf) {
        Remove-Item -LiteralPath $launchPath -Force
    }
}

function Test-HubActivityUi {
    $headers = @{ Authorization = "Bearer $(Get-AdminToken)" }
    $status = Invoke-RestMethod -Uri "$(Get-HubUrl)/dashboard/api/status" -Method Get -Headers $headers -TimeoutSec 5
    return [bool]$status.extensions.activityUi
}

function Start-HubTask {
    param([switch]$SkipPrepare, [switch]$Ui)

    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if (-not $task) {
        throw "Hub task is not installed. Run: hubctl install"
    }
    if (-not $Ui) { Set-LaunchMode }
    if ($Ui -and (Test-HubHealth) -and -not (Test-HubActivityUi)) {
        Write-Output "The Hub is running in fast mode without the activity UI; restarting it with -Ui."
        Stop-HubTask
    }
    if (-not (Test-HubHealth)) {
        if (-not $SkipPrepare) { Initialize-HubEnvironment }
        Set-LaunchMode -Ui:$Ui
        Start-ScheduledTask -TaskName $taskName
        $deadline = (Get-Date).AddSeconds(120)
        while ((Get-Date) -lt $deadline -and -not (Test-HubHealth)) { Start-Sleep -Milliseconds 500 }
        if (-not (Test-HubHealth)) { throw "The Hub did not become healthy; run hubctl status for task state." }
        Restore-StatusApp
    }
}

function Stop-HubTask {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if (Test-HubHealth) {
        $headers = @{ Authorization = "Bearer $(Get-AdminToken)" }
        $response = Invoke-RestMethod -Uri "$(Get-HubUrl)/dashboard/api/service/shutdown" -Method Post -Headers $headers -TimeoutSec 5
        if (-not $response.accepted) { throw "The Hub rejected the graceful shutdown request." }
    } elseif ($task -and [string]$task.State -eq "Running") {
        throw "The Hub task is running but its health endpoint is unavailable; inspect the Hub log before stopping it."
    } else {
        return
    }

    $deadline = (Get-Date).AddSeconds(45)
    while ((Get-Date) -lt $deadline) {
        $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        if ((-not $task -or [string]$task.State -ne "Running") -and -not (Test-HubHealth)) { return }
        Start-Sleep -Milliseconds 250
    }
    throw "The Hub did not shut down gracefully within 45 seconds."
}

function Get-PythonW {
    $basePython = & $venvPython -c "import sys; print(sys._base_executable)"
    if ($LASTEXITCODE -ne 0 -or -not $basePython) { throw "Could not find the prepared Python runtime." }
    $pythonw = Join-Path (Split-Path ([string]$basePython)) "pythonw.exe"
    if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf)) { throw "The prepared Python runtime has no pythonw.exe." }
    return $pythonw
}

function Protect-PrivateConfig {
    param([string]$UserId)

    # Private files hold tokens and API keys; restrict them to this user and SYSTEM.
    $privateRoot = Join-Path $repoRoot "config\private"
    New-Item -ItemType Directory -Force -Path $privateRoot | Out-Null
    icacls $privateRoot /inheritance:r /grant:r "${UserId}:(OI)(CI)F" "*S-1-5-18:(OI)(CI)F" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not restrict access to config\private." }
}

function Test-OthersCanWrite {
    param([string]$Path)

    # Write, append, delete, ACL/owner changes, and the generic write/all bits used by inherit-only entries.
    $writeMask = 0x2 -bor 0x4 -bor 0x10 -bor 0x100 -bor 0x10000 -bor 0x40000 -bor 0x80000 -bor 0x40000000 -bor 0x10000000
    $trusted = @([Security.Principal.WindowsIdentity]::GetCurrent().User.Value, "S-1-5-18", "S-1-5-32-544", "S-1-3-0")
    foreach ($rule in (Get-Acl -LiteralPath $Path).Access) {
        if ([string]$rule.AccessControlType -ne "Allow") { continue }
        try { $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value } catch { continue }
        if ($trusted -contains $sid) { continue }
        if (([int64]$rule.FileSystemRights -band $writeMask) -ne 0) { return $true }
    }
    return $false
}

function Protect-HubCheckout {
    param([string]$UserId)

    # The logon task runs code from this checkout as this user; another account able to edit it could run code as us.
    if (Test-OthersCanWrite -Path $repoRoot) {
        icacls $repoRoot /inheritance:r /grant:r "${UserId}:(OI)(CI)F" "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" | Out-Null
        if ($LASTEXITCODE -ne 0 -or (Test-OthersCanWrite -Path $repoRoot)) {
            $acl = Get-Acl -LiteralPath $repoRoot
            $trusted = @([Security.Principal.WindowsIdentity]::GetCurrent().User.Value, "S-1-5-18", "S-1-5-32-544", "S-1-3-0")
            $untrustedRules = @($acl.Access | Where-Object {
                if ([string]$_.AccessControlType -ne "Allow") { return $false }
                try {
                    $sid = $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
                    return -not ($trusted -contains $sid)
                } catch { return $true }
            })
            foreach ($rule in $untrustedRules) { [void]$acl.RemoveAccessRule($rule) }
            Set-Acl -LiteralPath $repoRoot -AclObject $acl
            if ((Test-OthersCanWrite -Path $repoRoot)) { throw "Could not restrict write access to the Hub checkout." }
        }
        Write-Output "Restricted the Hub checkout to this user, SYSTEM, and Administrators."
    }
    $parent = Split-Path -Parent $repoRoot
    if ($parent -and (Test-OthersCanWrite -Path $parent)) {
        $command = 'icacls <folder> /inheritance:r /grant:r "' + $UserId + ':(OI)(CI)F" *S-1-5-18:(OI)(CI)F *S-1-5-32-544:(OI)(CI)F'
        Write-Warning ("Other accounts can write to '" + $parent + "', which holds the child MCP repositories the Hub runs as you. " +
            "Restrict it, or each child repository, the same way: " + $command)
    }
}

function Test-Autostart {
    param([string]$Name)

    $task = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
    if (-not $task) { return $false }
    return @($task.Triggers | Where-Object { $_.Enabled }).Count -gt 0
}

function Set-Autostart {
    param([string]$Name, [bool]$Enabled)

    # Only the logon trigger is switched; the task stays enabled, so hubctl start and the app's Start button still run it.
    $task = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
    if (-not $task) { throw "The $Name task is not installed. Run: hubctl install" }
    foreach ($trigger in $task.Triggers) { $trigger.Enabled = $Enabled }
    Set-ScheduledTask -InputObject $task | Out-Null
}

function Write-Autostart {
    $state = [ordered]@{}
    foreach ($target in $autostartTasks.Keys) { $state[$target] = Test-Autostart -Name $autostartTasks[$target] }
    Write-Output ([pscustomobject]$state | ConvertTo-Json -Compress)
}

function New-LogonTrigger {
    param([string]$Name, [string]$UserId)

    # Registering again (hubctl install) keeps an autostart the user turned off.
    $wasOff = (Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue) -and -not (Test-Autostart -Name $Name)
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $UserId
    $trigger.Enabled = -not $wasOff
    return $trigger
}

function Register-HubTask {
    $serviceLauncher = Join-Path $PSScriptRoot "service_launcher.pyw"
    $action = New-ScheduledTaskAction -Execute (Get-PythonW) -Argument ('"{0}"' -f $serviceLauncher) -WorkingDirectory $repoRoot
    $userId = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    Protect-HubCheckout -UserId $userId
    Protect-PrivateConfig -UserId $userId
    $trigger = New-LogonTrigger -Name $taskName -UserId $userId
    $principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
    # A crashed service is restarted up to 3 times, one minute apart.
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
}

function Remove-LegacyStatusAppTask {
    # The desktop app is a separate product now; its installer adds its own sign-in entry.
    if (Get-ScheduledTask -TaskName $statusAppTaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $statusAppTaskName -Confirm:$false
    }
}

function Get-InstalledStatusApp {
    $installPath = (Get-ItemProperty -Path $statusAppKey -Name InstallPath -ErrorAction SilentlyContinue).InstallPath
    if (-not $installPath) { return $null }
    $exe = Join-Path $installPath "mcp-hub-status.exe"
    if (Test-Path -LiteralPath $exe -PathType Leaf) { return $exe }
    return $null
}

function Start-SourceStatusApp {
    # pythonw has no console; the app reaches the Hub only through its connection file and HTTP API.
    $pythonw = Join-Path $repoRoot ".venv\Scripts\pythonw.exe"
    if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf)) { throw "The Hub environment has no pythonw.exe. Run: hubctl install" }
    Start-Process -FilePath $pythonw -ArgumentList "-m mcp_hub.desktop" -WorkingDirectory $repoRoot
}

function Open-StatusApp {
    # The app keeps a single instance, so opening it again while it runs opens nothing new.
    $exe = Get-InstalledStatusApp
    if ($exe) { Start-Process -FilePath $exe } else { Start-SourceStatusApp }
}

function Get-ReleasesUrl {
    # The Releases page of this checkout's GitHub repository, read from its git remote; $null without one.
    $git = Get-Command git -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $git) { return $null }
    $remote = [string](& $git.Source -C $repoRoot remote get-url origin 2>$null)
    if ($LASTEXITCODE -ne 0) { return $null }
    if ($remote -match 'github\.com[:/]([\w.-]+)/([\w.-]+?)(?:\.git)?/?$') {
        return "https://github.com/$($Matches[1])/$($Matches[2])/releases"
    }
    return $null
}

function Write-StatusAppHint {
    if (Get-InstalledStatusApp) { return }
    $releases = Get-ReleasesUrl
    $where = if ($releases) { "$releases/latest" } else { "the Releases page of your mcp-hub repository" }
    Write-Output "Desktop status app: not installed. Download mcp-hub-status-setup-<version>.exe from $where, or run: hubctl app -Install"
}

function Get-LatestInstaller {
    param([string]$Releases)

    $api = $Releases -replace '^https://github\.com/', 'https://api.github.com/repos/'
    try {
        $release = Invoke-RestMethod -Uri "$api/latest" -Headers @{ Accept = "application/vnd.github+json" } -TimeoutSec 30
    } catch {
        throw "Could not read the latest release (a private repository needs a manual download): $Releases"
    }
    # The name is checked before it becomes a file name.
    $setup = @($release.assets | Where-Object { $_.name -match '^mcp-hub-status-setup-\d+\.\d+\.\d+\.exe$' })[0]
    $sum = if ($setup) { @($release.assets | Where-Object { $_.name -eq "$($setup.name).sha256" })[0] } else { $null }
    if (-not $setup -or -not $sum) { throw "The latest release has no installer with its SHA-256: $Releases" }
    return $setup, $sum
}

function Find-LocalInstaller {
    $dist = Join-Path $repoRoot "dist"
    if (Test-Path -LiteralPath $dist -PathType Container) {
        $local = @(Get-ChildItem -LiteralPath $dist -Filter "mcp-hub-status-setup-*.exe" -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending)
        if ($local.Count -gt 0) { return $local[0].FullName }
    }
    return $null
}

function Install-ReleasedStatusApp {
    param([string]$Releases)
    $setup, $sum = Get-LatestInstaller -Releases $Releases
    $folder = Join-Path $env:TEMP "mcp-hub-status-setup"
    New-Item -ItemType Directory -Force -Path $folder | Out-Null
    $path = Join-Path $folder $setup.name
    Invoke-WebRequest -Uri $setup.browser_download_url -OutFile $path -UseBasicParsing
    Invoke-WebRequest -Uri $sum.browser_download_url -OutFile "$path.sha256" -UseBasicParsing
    $expected = ((Get-Content -LiteralPath "$path.sha256" -Raw).Trim() -split '\s+')[0].ToLowerInvariant()
    $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $expected) {
        Remove-Item -LiteralPath $path -Force
        throw "The downloaded installer does not match its SHA-256; it was deleted and not run."
    }
    Write-Output "Running $($setup.name), checked against its SHA-256."
    Start-Process -FilePath $path -Wait
}

function Install-StatusApp {
    # The published Release comes first: it is the installer for the version in pyproject.toml. A build left in
    # dist/ is used only when no Release can be installed (no GitHub remote, or the download failed).
    $releases = Get-ReleasesUrl
    if ($releases) {
        try {
            Install-ReleasedStatusApp -Releases $releases
            return
        } catch {
            Write-Warning "Could not install desktop app from GitHub release: $($_.Exception.Message)."
        }
    } else {
        Write-Warning "This checkout has no GitHub remote."
    }
    $localInstaller = Find-LocalInstaller
    if ($localInstaller) {
        Write-Warning "Running the build in dist/, which may be older than the release: $localInstaller"
        Start-Process -FilePath $localInstaller -Wait
        return
    }
    Write-Warning "Falling back to running the app from source."
    Start-SourceStatusApp
}

function Write-ConnectedClients {
    $headers = @{ Authorization = "Bearer $(Get-AdminToken)" }
    $status = Invoke-RestMethod -Uri "$(Get-HubUrl)/dashboard/api/status" -Method Get -Headers $headers -TimeoutSec 5
    if (-not $status.extensions.activityUi) {
        Write-Output "Mode: fast (activity UI off; turn on Track AI clients in the desktop app, or start with -Ui)"
        return
    }
    Write-Output "Mode: activity UI on"
    $clients = @($status.clients)
    $live = @($clients | Where-Object live | ForEach-Object { if ($_.connections -gt 1) { "$($_.client) x$($_.connections)" } else { $_.client } })
    $recent = @($clients | Where-Object { -not $_.live } | ForEach-Object client)
    Write-Output ("Clients: {0}{1}" -f $live.Count, $(if ($live.Count) { " ($($live -join ', '))" } else { "" }))
    if ($recent.Count) { Write-Output ("Recently active (30 min): {0}" -f ($recent -join ", ")) }
}

function Write-HubStatus {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    $taskState = if ($task) { [string]$task.State } else { "NotInstalled" }
    $health = Test-HubHealth
    Write-Output ("Task: {0}; HTTP health: {1}" -f $taskState, $(if ($health) { "healthy" } else { "unavailable" }))
    if ($health) { Write-ConnectedClients }
}

function Open-HubDashboard {
    param([switch]$Ui)

    if ($Ui -or -not (Test-HubHealth)) { Start-HubTask -Ui:$Ui }
    if (-not (Test-HubHealth)) { throw "The Hub did not become healthy; run hubctl status for task state." }
    $headers = @{ Authorization = "Bearer $(Get-AdminToken)" }
    $pairing = Invoke-RestMethod -Uri "$(Get-HubUrl)/dashboard/pair" -Method Post -Headers $headers -TimeoutSec 5
    if (-not $pairing.pairing_token) { throw "Dashboard pairing failed; run hubctl status for service state." }
    $pair = [uri]::EscapeDataString([string]$pairing.pairing_token)
    Start-Process "$(Get-HubUrl)/dashboard/?pair=$pair"
}

switch ($Command) {
    "install" {
        # Stop first: a running service locks .venv\Scripts\mcp-hub-service.exe, which uv sync may need to replace.
        if (Test-HubHealth) { Stop-HubTask }
        Initialize-HubEnvironment
        Register-HubTask
        Remove-LegacyStatusAppTask
        Start-HubTask -SkipPrepare -Ui:$Ui
        Open-StatusApp
        Write-Output "Installed and started the per-user mcp-hub task."
        Write-StatusAppHint
    }
    "status" {
        Write-HubStatus
        if (-not (Test-HubHealth)) { exit 1 }
    }
    "preflight" { exit (Invoke-Preflight) }
    "start" {
        Start-HubTask -Ui:$Ui
        Write-HubStatus
    }
    "stop" {
        Stop-HubTask
        Write-Output "Stopped the mcp-hub scheduled task."
    }
    "restart" {
        Stop-HubTask
        Start-HubTask -Ui:$Ui
        Write-Output "Restarted the mcp-hub scheduled task."
    }
    "open" { Open-HubDashboard -Ui:$Ui }
    "app" {
        if ($Install) { Install-StatusApp } else { Open-StatusApp; Write-StatusAppHint }
    }
    "autostart" {
        if ($Target) {
            if (-not $State) { throw "Choose the new state: hubctl autostart -Target $Target -State on|off" }
            Set-Autostart -Name $autostartTasks[$Target] -Enabled ($State -eq "on")
        }
        Write-Autostart
    }
}
