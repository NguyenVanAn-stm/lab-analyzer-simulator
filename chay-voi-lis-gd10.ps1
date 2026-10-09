# Chay may xet nghiem gia (lab-analyzer-simulator) -> Connector LIS GD10, cong Generic 5101 (SIM-01).
# Yeu cau: stack lis-gd10 dang chay, Connector da map 5101.
# UI http://127.0.0.1:8000 : nhap barcode ong da tiep nhan, profile chemistry-normal.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "Chua co .venv - tao va cai dat..." -ForegroundColor Yellow
    python -m venv .venv
    & $venvPython -m pip install -e ".[dev]"
}

# SIM-01 Generic tren 5101. KHONG dung 5100 (AlinityCi / STM.Lis.AlinitySimulator).
$env:SIMULATOR_PROTOCOL = "astm"
$env:LAB_SIM_MODE = "live"
$env:LAB_SIM_GATEWAY_HOST = "127.0.0.1"
$env:LAB_SIM_GATEWAY_PORT = "5101"
$env:LAB_SIM_OPERATOR = "lis.ktv"

$gwHost = $env:LAB_SIM_GATEWAY_HOST
$gwPort = $env:LAB_SIM_GATEWAY_PORT
Write-Host "Mo http://127.0.0.1:8000 | Connector SIM-01 tai ${gwHost}:${gwPort}" -ForegroundColor Cyan
& $venvPython -m lab_analyzer_simulator --root . --config config.lis-gd10.json web
