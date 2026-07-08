$ErrorActionPreference = "Stop"

# Always run the application from the folder containing this script. This
# prevents Python from importing an older copy of app.main from another folder.
Set-Location -LiteralPath $PSScriptRoot

$version = (Get-Content -LiteralPath ".\VERSION" -Raw).Trim()
Write-Host "Starting ASOC PI Readiness build v$version" -ForegroundColor Green
Write-Host "Application folder: $PSScriptRoot"

# Refuse to silently leave an older server running on the same port.
try {
    $listener = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($listener) {
        Write-Host "Port 8000 is already being used by process $($listener.OwningProcess)." -ForegroundColor Red
        Write-Host "Stop the old app first, then run this script again." -ForegroundColor Yellow
        Write-Host "Command: Stop-Process -Id $($listener.OwningProcess) -Force"
        exit 1
    }
} catch {
    # Continue on Windows editions where Get-NetTCPConnection is unavailable.
}

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -m venv .venv
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        & python -m venv .venv
    } else {
        throw "Python was not found. Install Python 3.11 or newer and try again."
    }
}

& $venvPython -m pip install -r requirements.txt
if (-not (Test-Path -LiteralPath ".env")) { Copy-Item ".env.example" ".env" }

Write-Host "Open http://127.0.0.1:8000 after the server starts." -ForegroundColor Cyan
Write-Host "The page header must show v$version. Health check: http://127.0.0.1:8000/health" -ForegroundColor Cyan
& $venvPython -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
