@echo off
rem Chay giao dien web cua bo gia lap may xet nghiem. Mo http://127.0.0.1:8000
rem Tham so tuy chon: %1 = hl7 hoac astm (mac dinh astm). Dat LAS_KHONG_MO_TRINH_DUYET=1 de khong tu mo trinh duyet.
chcp 65001 >nul
cd /d "%~dp0"
set "PROTO=%~1"
if "%PROTO%"=="" set "PROTO=astm"
if not exist ".venv\Scripts\python.exe" (
  echo Chua co moi truong .venv - dang tao va cai dat...
  python -m venv .venv || goto :loi
  ".venv\Scripts\python.exe" -m pip install -q -e . || goto :loi
)
if not "%LAS_KHONG_MO_TRINH_DUYET%"=="1" start "" http://127.0.0.1:8000
".venv\Scripts\python.exe" -m lab_analyzer_simulator --root . --protocol %PROTO% web
goto :eof
:loi
echo Loi khi tao moi truong Python.
pause
