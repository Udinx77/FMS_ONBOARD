@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title FMS Onboard Monitoring - Launcher

rem ===== 1. cari Python =====
set "PY="
python --version >nul 2>nul && set "PY=python"
if not defined PY py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY (
    echo [ERROR] Python tidak ditemukan. Pasang Python 3.10 atau lebih baru dan centang "Add python.exe to PATH".
    pause
    exit /b 1
)

rem ===== 2. pemeriksaan file =====
if not exist ".env" (
    echo [ERROR] File .env belum ada. Salin .env.example menjadi .env, lalu isi koneksi SQL Server.
    pause
    exit /b 1
)
if not exist "dataoidip.xlsx" echo [INFO] dataoidip.xlsx tidak ditemukan di folder ini. IP GPS tidak akan dipantau.

rem ===== 3. dependensi (dipasang otomatis jika belum ada) =====
%PY% -c "import flask, flask_socketio, pandas, openpyxl, reportlab, icmplib, pyodbc" >nul 2>nul
if errorlevel 1 (
    echo [INFO] Memasang dependensi dari requirements.txt ...
    %PY% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Pemasangan dependensi gagal. Periksa koneksi internet atau pasang manual: pip install -r requirements.txt
        pause
        exit /b 1
    )
)

rem ===== 4. port dashboard (FLASK_PORT di .env, bawaan 5001) =====
set "PORT=5001"
for /f "usebackq tokens=1,* delims==" %%a in (`findstr /b /i "FLASK_PORT=" .env`) do set "PORT=%%b"
set "PORT=%PORT: =%"

rem ===== 5. jalankan =====
echo [1/3] Menjalankan collector ping ...
start "FMS Collector Ping" cmd /k %PY% collect_ping.py
echo [2/3] Menunggu collector siap (8 detik) ...
timeout /t 8 /nobreak >nul
echo [3/3] Menjalankan dashboard ...
start "FMS Dashboard" cmd /k %PY% app.py
timeout /t 8 /nobreak >nul
start "" "http://localhost:%PORT%/"

echo.
echo Sistem berjalan. Dashboard: http://localhost:%PORT%/
echo Untuk menghentikan, tutup jendela "FMS Collector Ping" dan "FMS Dashboard".
timeout /t 5 >nul
endlocal
