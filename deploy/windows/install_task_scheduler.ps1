# ==============================================================================
# Funbot Systematic Solana Trading Engine - Task Scheduler Setup
# Registers a high-reliability Windows Scheduled Task for ProductionSupervisor.
# ==============================================================================

param(
    [string]$TaskName = "FunbotProductionSupervisor",
    [switch]$Uninstall = $false
)

$ProjectRoot = Resolve-Path "$PSScriptRoot\..\.."
$LauncherScript = Join-Path $ProjectRoot "deploy\windows\run_supervisor.bat"

if ($Uninstall) {
    Write-Host "[Funbot] Unregistering Scheduled Task: $TaskName..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "[Funbot] Scheduled Task $TaskName removed." -ForegroundColor Green
    exit 0
}

if (-not (Test-Path $LauncherScript)) {
    Write-Error "[Funbot] Launcher script not found at: $LauncherScript"
    exit 1
}

Write-Host "[Funbot] Registering Scheduled Task: $TaskName" -ForegroundColor Cyan
Write-Host "  Project Root:    $ProjectRoot"
Write-Host "  Launcher Script: $LauncherScript"

# Define Action
$Action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"$LauncherScript`"" `
    -WorkingDirectory "$ProjectRoot"

# Define Trigger (At System Startup and Logon)
$TriggerStartup = New-ScheduledTaskTrigger -AtStartup
$TriggerLogon = New-ScheduledTaskTrigger -AtLogOn

# Define Settings (Restart on failure, run indefinitely)
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 365) `
    -Priority 4

# Define Principal (Run with standard or current user privileges)
$Principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

# Register Task
Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger @($TriggerStartup, $TriggerLogon) `
    -Settings $Settings `
    -Principal $Principal `
    -Force

Write-Host "[Funbot] Successfully registered Scheduled Task '$TaskName'." -ForegroundColor Green
Write-Host "  To start task immediately: Start-ScheduledTask -TaskName `"$TaskName`""
Write-Host "  To inspect task:           Get-ScheduledTask -TaskName `"$TaskName`""
Write-Host "  To remove task:            .\install_task_scheduler.ps1 -Uninstall"
