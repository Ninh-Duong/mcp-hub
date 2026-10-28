param(
    [Parameter(Position = 0)]
    [ValidateSet("install", "status", "start", "stop", "restart", "open")]
    [string]$Command = "status"
)

$ErrorActionPreference = "Stop"
$taskName = "mcp-hub-shared-service"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$startScript = Join-Path $PSScriptRoot "start.ps1"
$url = "http://127.0.0.1:8765"

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

function Start-HubTask {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if (-not $task) {
        throw "Hub task is not installed. Run: hubctl install"
    }
    if (-not (Test-HubHealth)) {
        Start-ScheduledTask -TaskName $taskName
        $deadline = (Get-Date).AddSeconds(120)
        while ((Get-Date) -lt $deadline -and -not (Test-HubHealth)) { Start-Sleep -Milliseconds 500 }
        if (-not (Test-HubHealth)) { throw "The Hub did not become healthy; run hubctl status for task state." }
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

switch ($Command) {
    "install" {
        $powershell = (Get-Command powershell.exe -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
        $quotedScript = '"{0}"' -f $startScript
        $arguments = "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File $quotedScript -Service"
        $action = New-ScheduledTaskAction -Execute $powershell -Argument $arguments -WorkingDirectory $repoRoot
        $userId = [Security.Principal.WindowsIdentity]::GetCurrent().Name
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $userId
        $principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
        $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero)
        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
        Start-HubTask
        Write-Output "Installed and started the per-user mcp-hub task."
    }
    "status" {
        $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        $taskState = if ($task) { [string]$task.State } else { "NotInstalled" }
        $health = Test-HubHealth
        Write-Output ("Task: {0}; HTTP health: {1}" -f $taskState, $(if ($health) { "healthy" } else { "unavailable" }))
        if (-not $health) { exit 1 }
    }
    "start" { Start-HubTask }
    "stop" {
        Stop-HubTask
        Write-Output "Stopped the mcp-hub scheduled task."
    }
    "restart" {
        Stop-HubTask
        Start-HubTask
        Write-Output "Restarted the mcp-hub scheduled task."
    }
    "open" {
        if (-not (Test-HubHealth)) {
            Start-HubTask
        }
        if (-not (Test-HubHealth)) { throw "The Hub did not become healthy; run hubctl status for task state." }
        $headers = @{ Authorization = "Bearer $(Get-ServiceToken)" }
        $pairing = Invoke-RestMethod -Uri "$url/dashboard/pair" -Method Post -Headers $headers -TimeoutSec 5
        if (-not $pairing.pairing_token) { throw "Dashboard pairing failed; run hubctl status for service state." }
        $pair = [uri]::EscapeDataString([string]$pairing.pairing_token)
        Start-Process "$url/dashboard/?pair=$pair"
    }
}
