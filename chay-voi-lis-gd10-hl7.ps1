# May gia HL7/MLLP -> Connector lis-gd10 cong 2575 (SIM-02-HL7).
# Yeu cau: ong da tiep nhan tren LIS; KHONG dung ong da ASTM xong (Resulted).
# UI http://127.0.0.1:8000 - barcode vd STM261009000002

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "Chua co .venv - tao va cai dat..." -ForegroundColor Yellow
    python -m venv .venv
    & $venvPython -m pip install -e ".[dev]"
}

# Luon 2575 - KHONG giu LAB_SIM_GATEWAY_PORT=5101 sau khi chay ASTM.
$env:SIMULATOR_PROTOCOL = "hl7"
$env:LAB_SIM_MODE = "live"
$env:LAB_SIM_GATEWAY_HOST = "127.0.0.1"
$env:LAB_SIM_GATEWAY_PORT = "2575"
$env:LAB_SIM_OPERATOR = "lis.ktv"

$gwHost = $env:LAB_SIM_GATEWAY_HOST
$gwPort = $env:LAB_SIM_GATEWAY_PORT
Write-Host "Mo http://127.0.0.1:8000 | Connector SIM-02-HL7 (HL7/MLLP) tai ${gwHost}:${gwPort}" -ForegroundColor Cyan
Write-Host "Neu vua chay ASTM: dong tab simulator cu hoac doi port - ASTM=5101, HL7=2575." -ForegroundColor DarkYellow
& $venvPython -m lab_analyzer_simulator --root . --config config.lis-gd10-hl7.json web

