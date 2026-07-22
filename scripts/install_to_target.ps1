param(
    [string]$Target = "C:\Nestjs\Advanced_Btc_Bot\macro_b3_bot"
)

$ErrorActionPreference = "Stop"
$Source = Split-Path -Parent $PSScriptRoot

if (Test-Path $Target) {
    throw "Target already exists: $Target. Refusing to overwrite."
}

New-Item -ItemType Directory -Force -Path $Target | Out-Null
Copy-Item -Path "$Source\*" -Destination $Target -Recurse -Force
Write-Host "Project copied to $Target"
Write-Host "Run: cd $Target; .\scripts\bootstrap_windows.ps1"
