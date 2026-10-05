@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul

cd /d "%~dp0"

set "OVERALL_STATUS=PASSED"
set "DATA_GEN_STATUS=SKIPPED"
set "BACKTEST_STATUS=SKIPPED"
set "TESTS_STATUS=SKIPPED"
set "API_STATUS=SKIPPED"
set "UI_STATUS=SKIPPED"

echo ================================================================================
echo   TENANT-AWARE STORAGE AND CAPACITY FORECASTER
echo   Automated End-to-End Execution Pipeline
echo ================================================================================
echo [%date% %time%] Running from: %cd%

echo.
echo ================================================================================
echo   [PHASE 0] PYTHON DETECTION AND ENVIRONMENT SETUP
echo ================================================================================
echo [%date% %time%] Detecting Python interpreter...

set "PY="

py -3 --version >nul 2>&1
if !ERRORLEVEL! equ 0 (
    set "PY=py -3"
    goto :python_found
)

python --version >nul 2>&1
if !ERRORLEVEL! equ 0 (
    set "PY=python"
    goto :python_found
)

if exist "C:\Users\FRANKY\AppData\Local\Python\bin\python.exe" (
    "C:\Users\FRANKY\AppData\Local\Python\bin\python.exe" --version >nul 2>&1
    if !ERRORLEVEL! equ 0 (
        set "PY=C:\Users\FRANKY\AppData\Local\Python\bin\python.exe"
        goto :python_found
    )
)

echo [ERROR] Python not found via 'py -3', 'python', or 'C:\Users\FRANKY\AppData\Local\Python\bin\python.exe'.
echo Please ensure Python 3.11+ is installed.
exit /b 1

:python_found
echo [%date% %time%] Using Python command: %PY%

if not exist ".venv" (
    echo [%date% %time%] Creating virtual environment in .venv...
    %PY% -m venv .venv
    if !ERRORLEVEL! neq 0 (
        echo [ERROR] Failed to create virtual environment in .venv.
        exit /b 1
    )
) else (
    echo [%date% %time%] Virtual environment .venv already exists. Skipping creation.
)

echo [%date% %time%] Activating virtual environment...
call .venv\Scripts\activate.bat
if !ERRORLEVEL! neq 0 (
    echo [ERROR] Failed to activate virtual environment.
    exit /b 1
)

echo [%date% %time%] Upgrading pip silently...
set PIP_PROGRESS_BAR=off
python -m pip install --upgrade pip --quiet

echo [%date% %time%] Installing project dependencies...
if exist "requirements.txt" (
    python -m pip install -r requirements.txt
) else (
    echo [%date% %time%] requirements.txt not found. Falling back to default packages...
    python -m pip install fastapi uvicorn sqlalchemy streamlit pandas numpy scikit-learn statsmodels matplotlib seaborn pytest httpx altair
)

if not exist "data" mkdir data
if not exist "docs" mkdir docs

echo.
echo ================================================================================
echo   [PHASE 1] DATA GENERATION
echo ================================================================================
echo [%date% %time%] Generating synthetic multi-tenant dataset...
python scripts\generate_data.py
set "DATA_GEN_EXIT=!ERRORLEVEL!"

if !DATA_GEN_EXIT! neq 0 (
    echo [FAIL] Data generation failed with exit code !DATA_GEN_EXIT!.
    set "DATA_GEN_STATUS=FAILED (code !DATA_GEN_EXIT!)"
    set "OVERALL_STATUS=FAILED"
    goto :summary
) else (
    echo [PASS] Data generation succeeded.
    set "DATA_GEN_STATUS=PASSED"
)

echo.
echo ================================================================================
echo   [PHASE 2] BACKTEST BENCHMARK EVALUATION (70/30 SPLIT)
echo ================================================================================
echo [%date% %time%] Running backtest benchmark (Baseline vs Proposed)...
python scripts\run_backtest.py
set "BACKTEST_EXIT=!ERRORLEVEL!"

if !BACKTEST_EXIT! neq 0 (
    echo [FAIL] Backtest failed with exit code !BACKTEST_EXIT!.
    set "BACKTEST_STATUS=FAILED (code !BACKTEST_EXIT!)"
    set "OVERALL_STATUS=FAILED"
) else (
    echo [PASS] Backtest succeeded.
    set "BACKTEST_STATUS=PASSED"
)

echo.
echo ================================================================================
echo   [PHASE 3] UNIT AND INTEGRATION TESTS
echo ================================================================================
echo [%date% %time%] Running pytest test suite...
python -m pytest -v tests\test_forecast.py --tb=short
set "TEST_EXIT=!ERRORLEVEL!"

if !TEST_EXIT! neq 0 (
    echo [FAIL] Unit tests failed with exit code !TEST_EXIT!.
    set "TESTS_STATUS=FAILED (code !TEST_EXIT!)"
    set "OVERALL_STATUS=FAILED"
) else (
    echo [PASS] All unit and edge-case tests passed.
    set "TESTS_STATUS=PASSED"
)

echo.
echo ================================================================================
echo   [PHASE 4] FASTAPI BACKGROUND START AND SMOKE TEST
echo ================================================================================
echo [%date% %time%] Launching FastAPI backend on port 8000 in background...
start "API" /min cmd /c "python -m uvicorn app.main:app --port 8000"

echo [%date% %time%] Waiting 5 seconds for API service startup...
ping 127.0.0.1 -n 6 >nul 2>&1

echo [%date% %time%] Performing HTTP API smoke test...
python -c "import urllib.request, json; h = urllib.request.urlopen('http://localhost:8000/health'); print('  [Health Check Response]:', h.getcode(), h.read().decode().strip()); req = urllib.request.Request('http://localhost:8000/api/tenants', headers={'X-Role': 'platform_admin'}); t = urllib.request.urlopen(req); data = json.loads(t.read().decode()); print('  [Tenants API Response]: ', t.getcode(), f'Successfully retrieved {len(data)} tenants with X-Role: platform_admin.')"
set "SMOKE_EXIT=!ERRORLEVEL!"

if !SMOKE_EXIT! neq 0 (
    echo [FAIL] API smoke test failed.
    set "API_STATUS=FAILED"
    set "OVERALL_STATUS=FAILED"
) else (
    echo [PASS] API smoke test verified successfully.
    set "API_STATUS=PASSED"
)

set "API_PID="
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8000" ^| findstr "LISTENING"') do (
    if not defined API_PID set "API_PID=%%a"
)

if defined API_PID (
    echo [%date% %time%] FastAPI is running in background with PID: !API_PID!
    echo [%date% %time%] To stop the API later, run: taskkill /F /PID !API_PID!
) else (
    echo [%date% %time%] To stop background python servers later, run: taskkill /F /IM python.exe
)

echo.
echo ================================================================================
echo   [PHASE 5] STREAMLIT DASHBOARD LAUNCH
echo ================================================================================
echo [%date% %time%] Starting Streamlit UI service on port 8501 in background...
start "UI" /min cmd /c "streamlit run ui\streamlit_app.py --server.port 8501 --server.headless true"

echo [%date% %time%] Waiting 3 seconds for UI initialization...
ping 127.0.0.1 -n 4 >nul 2>&1

set "UI_STATUS=RUNNING"
echo.
echo --------------------------------------------------------------------------------
echo   ACTIVE SERVICE ENDPOINTS:
echo   - API:      http://localhost:8000
echo   - API docs: http://localhost:8000/docs
echo   - UI:       http://localhost:8501
echo --------------------------------------------------------------------------------
echo [%date% %time%] Opening Streamlit Dashboard in default web browser...
start http://localhost:8501

echo.
echo ================================================================================
echo   [PHASE 6] MEASURED BENCHMARK RESULTS AND DATABASE SUMMARY
echo ================================================================================
echo [%date% %time%] Formatted measured results from docs\measured_results.json:
echo.

if exist "docs\measured_results.json" (
    python -m json.tool docs\measured_results.json
) else (
    echo [WARNING] docs\measured_results.json not found.
)

echo.
echo [%date% %time%] Querying backtest_results table from data\saas_capacity.db:
echo.

python -c "import sqlite3; c = sqlite3.connect(r'data\saas_capacity.db'); rows = c.execute('SELECT model_name, mae_days, median_ae_days, within_14_days_pct, coverage_pct, false_urgent_pct FROM backtest_results').fetchall(); print(f'{\"Model Name\":<32} | {\"MAE\":<6} | {\"MedAE\":<6} | {\"Within 14d\":<10} | {\"Coverage\":<8} | {\"False Urgent\":<12}'); print('-'*88); [print(f'{r[0]:<32} | {r[1]:<6.1f} | {r[2]:<6.1f} | {r[3]:<10.1f} | {r[4]:<8.1f} | {r[5]:<12.1f}') for r in rows]"

:summary
echo.
echo ================================================================================
echo   PIPELINE EXECUTION SUMMARY
echo ================================================================================
echo [%date% %time%] Results Summary:
echo   - Phase 1 (Data Generation):   !DATA_GEN_STATUS!
echo   - Phase 2 (Backtest Engine):   !BACKTEST_STATUS!
echo   - Phase 3 (Unit Tests):        !TESTS_STATUS!
echo   - Phase 4 (API Smoke Test):    !API_STATUS!
echo   - Phase 5 (Streamlit UI):      !UI_STATUS!
echo.
if "!OVERALL_STATUS!"=="PASSED" (
    echo ================================================================================
    echo   [SUCCESS] ALL PHASES COMPLETED SUCCESSFULLY! SYSTEM READY FOR EXPLORATION.
    echo ================================================================================
) else (
    echo ================================================================================
    echo   [WARNING] PIPELINE COMPLETED WITH WARNINGS OR FAILURES. CHECK LOGS ABOVE.
    echo ================================================================================
)
