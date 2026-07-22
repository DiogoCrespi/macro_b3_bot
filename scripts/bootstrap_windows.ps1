$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv")) {
    py -3.11 -m venv .venv
}

& .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
}

Write-Host "Bootstrap completed. Edit .env, then run:"
Write-Host "  macro-b3 validate-config"
Write-Host "  macro-b3 discover-reuse --write-manifest"
Write-Host "  pytest"
