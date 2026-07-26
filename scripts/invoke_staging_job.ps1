[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string]$RunId,
    [string]$EnvFile = ".env.staging"
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot ".." )).Path
$envPath = if ([IO.Path]::IsPathRooted($EnvFile)) { $EnvFile } else { Join-Path $repo $EnvFile }
if (-not (Test-Path -LiteralPath $envPath)) { throw "Staging env file not found: $envPath" }

Get-Content -LiteralPath $envPath | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
        $key, $value = $line.Split("=", 2)
        [Environment]::SetEnvironmentVariable($key.Trim(), $value.Trim())
    }
}
[Environment]::SetEnvironmentVariable("STAGING_RUN_ID", $RunId)
Set-Location $repo
& python scripts/staging_worker.py
exit $LASTEXITCODE
