@echo off
REM ==============================================================================
REM Tenant-Aware Storage & Capacity Forecaster - Windows Batch Launcher
REM ==============================================================================
echo ================================================================================
echo   Tenant-Aware Storage & Capacity Forecaster - Launching System (Windows)
echo ================================================================================

cd /d "%~dp0"

REM Step 1: Detect Python
set PYTHON_CMD=py
where py >nul 2>nul
if %ERRORLEVEL% neq 0 (
    set PYTHON_CMD=python
    where python >nul 2>nul
    if %ERRORLEVEL% neq 0 (
        echo ERROR: Python is not found on your system PATH.
        exit /b 1
    )
)

echo [1/5] Using Python: %PYTHON_CMD%

REM Step 2: Virtual Environment
if not exist ".venv" (
    echo [2/5] Creating Python virtual environment in .venv...
    %PYTHON_CMD% -m venv .venv
) else (
    echo [2/5] Virtual environment already exists.
)

if exist ".venv\Scripts\python.exe" (
    set VENV_PYTHON=.venv\Scripts\python.exe
) else (
    set VENV_PYTHON=%PYTHON_CMD%
)

REM Step 3: Install Dependencies
echo [3/5] Installing / verifying dependencies...
%VENV_PYTHON% -m pip install -r requirements.txt --quiet

REM Step 4: Generate Synthetic Data & Run Backtest
echo [4/5] Generating synthetic data and executing 70/30 benchmark...
%VENV_PYTHON% scripts\generate_data.py
%VENV_PYTHON% scripts\run_backtest.py

REM Step 5: Start FastAPI and Streamlit
echo [5/5] Launching API and Streamlit UI...
start "Tenant Forecaster API" /b %VENV_PYTHON% -m uvicorn app.main:app --host 0.0.0.0 --port 8000
timeout /t 3 /nobreak >nul
start "Tenant Forecaster UI" %VENV_PYTHON% -m streamlit run ui\streamlit_app.py --server.port 8501

echo.
echo ================================================================================
echo   SYSTEM RUNNING!
echo   - FastAPI API:    http://localhost:8000/docs
echo   - Streamlit UI:   http://localhost:8501
echo ================================================================================
pause
