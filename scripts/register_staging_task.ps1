[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)] [string]$TaskName,
    [Parameter(Mandatory = $true)] [string]$RunId,
    [Parameter(Mandatory = $true)] [datetime]$At,
    [string]$EnvFile = ".env.staging"
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot ".." )).Path
$invoke = Join-Path $repo "scripts\invoke_staging_job.ps1"
if (-not (Test-Path -LiteralPath $invoke)) { throw "Invoker not found: $invoke" }
if ($At -lt (Get-Date)) { throw "-At must be in the future" }
if ($RunId -notmatch "^[A-Za-z0-9_.:-]+$") { throw "-RunId contains unsafe characters" }

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument (
    "-NoProfile -ExecutionPolicy Bypass -File `"$invoke`" -RunId `"$RunId`" -EnvFile `"$EnvFile`""
)
$trigger = New-ScheduledTaskTrigger -Once -At $At
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew
$task = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Description "Macro B3 staging one-shot run ($RunId)"

if ($PSCmdlet.ShouldProcess($TaskName, "Register Windows scheduled staging task")) {
    Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null
    Write-Output "registered task=$TaskName run_id=$RunId at=$($At.ToString('o'))"
} else {
    Write-Output "whatif task=$TaskName run_id=$RunId at=$($At.ToString('o'))"
}
