param(
    [Parameter(Position = 0)]
    [ValidateSet("install", "status", "start", "stop", "restart", "open", "preflight", "app", "autostart")]
    [string]$Command = "status",
    # start, restart, open, install: run the activity UI (barn, daily activity, connected clients) for this start,
    # even when the saved mode (desktop app: Settings > Hub > Track AI clients) is fast.
    [switch]$Ui,
    # autostart: which logon task to switch, and to which state. Without -Target it prints the current state.
    [ValidateSet("hub", "app")]
    [string]$Target,
    [ValidateSet("on", "off")]
    [string]$State
)

$ErrorActionPreference = "Stop"
$taskName = "mcp-hub"
$legacyTaskName = "mcp-hub-shared-service"
$statusAppTaskName = "mcp-hub-status"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$startScript = Join-Path $PSScriptRoot "start.ps1"
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$url = "http://127.0.0.1:8765"
$launchPath = Join-Path $repoRoot "config\private\launch.json"
$autostartTasks = [ordered]@{ hub = $taskName; app = $statusAppTaskName }

function Test-HubHealth {
    try {
        $response = Invoke-RestMethod -Uri "$url/health" -Method Get -TimeoutSec 2
        return $response.ok -eq $true
    } catch {
        return $false
    }
}

function Get-ServiceToken {
    $serviceConfigPath = Join-Path $repoRoot "config\private\service.json"
    if (-not (Test-Path -LiteralPath $serviceConfigPath -PathType Leaf)) {
        throw "Hub service config is missing; start the Hub first."
    }
    $serviceConfig = Get-Content -LiteralPath $serviceConfigPath -Raw | ConvertFrom-Json
    if (-not $serviceConfig.token) { throw "Hub service config is invalid; restart the Hub." }
    return [string]$serviceConfig.token
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
    if ($script:statusAppWasRunning -and (Get-ScheduledTask -TaskName $statusAppTaskName -ErrorAction SilentlyContinue)) {
        Start-ScheduledTask -TaskName $statusAppTaskName
    }
    $script:statusAppWasRunning = $false
}

function Initialize-HubEnvironment {
    $script:statusAppWasRunning = Stop-StatusApp
    Sync-HubEnvironment
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
    $headers = @{ Authorization = "Bearer $(Get-ServiceToken)" }
    $status = Invoke-RestMethod -Uri "$url/dashboard/api/status" -Method Get -Headers $headers -TimeoutSec 5
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
        $headers = @{ Authorization = "Bearer $(Get-ServiceToken)" }
        $response = Invoke-RestMethod -Uri "$url/dashboard/api/service/shutdown" -Method Post -Headers $headers -TimeoutSec 5
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
        if ($LASTEXITCODE -ne 0 -or (Test-OthersCanWrite -Path $repoRoot)) { throw "Could not restrict write access to the Hub checkout." }
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
    if (Get-ScheduledTask -TaskName $legacyTaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $legacyTaskName -Confirm:$false
    }
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

function Register-StatusAppTask {
    # The desktop status window (docs/desktop-app.md) opens at every logon. pythonw has no console, and the
    # venv's pythonw is not rewritten by uv sync the way a running entry-point exe would block it.
    $pythonw = Join-Path $repoRoot ".venv\Scripts\pythonw.exe"
    if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf)) { throw "The Hub environment has no pythonw.exe. Run: hubctl install" }
    $action = New-ScheduledTaskAction -Execute $pythonw -Argument "-m mcp_hub.desktop" -WorkingDirectory $repoRoot
    $userId = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $trigger = New-LogonTrigger -Name $statusAppTaskName -UserId $userId
    $principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $statusAppTaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
}

function Open-StatusApp {
    if (-not (Get-ScheduledTask -TaskName $statusAppTaskName -ErrorAction SilentlyContinue)) {
        throw "The desktop status app is not installed. Run: hubctl install"
    }
    # The app keeps a single instance, so starting it again while it runs opens nothing new.
    Start-ScheduledTask -TaskName $statusAppTaskName
}

function Write-ConnectedClients {
    $headers = @{ Authorization = "Bearer $(Get-ServiceToken)" }
    $status = Invoke-RestMethod -Uri "$url/dashboard/api/status" -Method Get -Headers $headers -TimeoutSec 5
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
    $headers = @{ Authorization = "Bearer $(Get-ServiceToken)" }
    $pairing = Invoke-RestMethod -Uri "$url/dashboard/pair" -Method Post -Headers $headers -TimeoutSec 5
    if (-not $pairing.pairing_token) { throw "Dashboard pairing failed; run hubctl status for service state." }
    $pair = [uri]::EscapeDataString([string]$pairing.pairing_token)
    Start-Process "$url/dashboard/?pair=$pair"
}

switch ($Command) {
    "install" {
        # Stop first: a running service locks .venv\Scripts\mcp-hub-service.exe, which uv sync may need to replace.
        if (Test-HubHealth) { Stop-HubTask }
        Initialize-HubEnvironment
        Register-HubTask
        Register-StatusAppTask
        Start-HubTask -SkipPrepare -Ui:$Ui
        Open-StatusApp
        Write-Output "Installed and started the per-user mcp-hub task and the desktop status app."
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
    "app" { Open-StatusApp }
    "autostart" {
        if ($Target) {
            if (-not $State) { throw "Choose the new state: hubctl autostart -Target $Target -State on|off" }
            Set-Autostart -Name $autostartTasks[$Target] -Enabled ($State -eq "on")
        }
        Write-Autostart
    }
}
