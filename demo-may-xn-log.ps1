# Log demo ro rang (tieng Viet) - thay cho console ASTM raw tren web UI.
# Can: stack lis-gd10, Connector 5101, ong da Tiep nhan tren LIS.

param(
    [Parameter(Mandatory = $true)]
    [string]$Barcode,
    [string]$Profile = "chemistry-normal",
    [switch]$QueryOnly
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "Chua co .venv - chay chay-voi-lis-gd10.ps1 mot lan truoc." -ForegroundColor Yellow
    exit 1
}

$env:SIMULATOR_PROTOCOL = "astm"
$env:LAB_SIM_MODE = "live"
$env:LAB_SIM_GATEWAY_HOST = "127.0.0.1"
$env:LAB_SIM_GATEWAY_PORT = "5101"
$env:LAB_SIM_OPERATOR = "lis.ktv"

$argsPy = @($Barcode, "--profile", $Profile)
if ($QueryOnly) { $argsPy += "--query-only" }

& $venvPython (Join-Path $PSScriptRoot "demo_lis_gd10.py") @argsPy
$code = $LASTEXITCODE

if ($code -eq 0) {
    Write-Host "--- Connector (8 dong gan nhat, SIM-01) ---" -ForegroundColor DarkCyan
    docker logs lis-gd10-connector-1 2>&1 | Select-String "SIM-01" | Select-Object -Last 8
}

exit $code
