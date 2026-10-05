#!/usr/bin/env bash
# ==============================================================================
# Tenant-Aware Storage & Capacity Forecaster
# End-to-End Execution Script
# ==============================================================================
set -e

# Change directory to project root if running from outside
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

echo "================================================================================"
echo "  Tenant-Aware Storage & Capacity Forecaster - Launching System"
echo "================================================================================"

# Step 1: Detect Python Executable
PYTHON_CMD=""
if command -v python3 &>/dev/null; then
    PYTHON_CMD="python3"
elif command -v python &>/dev/null; then
    PYTHON_CMD="python"
elif command -v py &>/dev/null; then
    PYTHON_CMD="py"
else
    echo "ERROR: Python was not found on your system PATH."
    exit 1
fi

echo "[1/6] Using Python: $($PYTHON_CMD --version)"

# Step 2: Set up Virtual Environment
VENV_DIR=".venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "[2/6] Creating Python virtual environment in $VENV_DIR..."
    $PYTHON_CMD -m venv "$VENV_DIR"
else
    echo "[2/6] Virtual environment already exists in $VENV_DIR."
fi

# Activate virtual environment
if [ -f "$VENV_DIR/bin/activate" ]; then
    source "$VENV_DIR/bin/activate"
    VENV_PYTHON="$VENV_DIR/bin/python"
elif [ -f "$VENV_DIR/Scripts/activate" ]; then
    source "$VENV_DIR/Scripts/activate"
    VENV_PYTHON="$VENV_DIR/Scripts/python.exe"
else
    VENV_PYTHON="$PYTHON_CMD"
fi

# Step 3: Install Dependencies
echo "[3/6] Installing / verifying dependencies from requirements.txt..."
$VENV_PYTHON -m pip install --upgrade pip --quiet
$VENV_PYTHON -m pip install -r requirements.txt --quiet

# Ensure data directory exists
mkdir -p data docs

# Step 4: Generate Synthetic Data
echo "[4/6] Generating synthetic multi-tenant dataset (50 tenants, 180 days)..."
$VENV_PYTHON scripts/generate_data.py

# Step 5: Run Backtest Benchmark
echo "[5/6] Running backtest experiment (Baseline vs Proposed Model)..."
$VENV_PYTHON scripts/run_backtest.py

# Step 6: Start FastAPI Backend and Streamlit Frontend
echo "[6/6] Starting application services..."

# Clean up any child processes on exit
cleanup() {
    echo ""
    echo "Shutting down Tenant Capacity Forecaster services..."
    if [ ! -z "$API_PID" ]; then
        kill "$API_PID" 2>/dev/null || true
    fi
    if [ ! -z "$UI_PID" ]; then
        kill "$UI_PID" 2>/dev/null || true
    fi
    exit 0
}
trap cleanup SIGINT SIGTERM EXIT

# Start FastAPI API in background
echo "Starting FastAPI backend on http://localhost:8000..."
$VENV_PYTHON -m uvicorn app.main:app --host 0.0.0.0 --port 8000 &
API_PID=$!

# Wait for API to become ready
echo "Waiting for API service to initialize..."
until curl -s http://localhost:8000/health >/dev/null 2>&1; do
    sleep 1
done
echo "FastAPI backend is ready."

# Start Streamlit UI in background or foreground
echo "Starting Streamlit UI on http://localhost:8501..."
$VENV_PYTHON -m streamlit run ui/streamlit_app.py --server.port 8501 --server.address 0.0.0.0 &
UI_PID=$!

echo ""
echo "================================================================================"
echo "  SYSTEM READY & RUNNING!"
echo "  - FastAPI Docs:     http://localhost:8000/docs"
echo "  - API Health:       http://localhost:8000/health"
echo "  - Streamlit UI:     http://localhost:8501"
echo "================================================================================"
echo "Press Ctrl+C to terminate both services."
echo ""

# Attempt to open browser if possible
if command -v xdg-open &>/dev/null; then
    xdg-open http://localhost:8501 2>/dev/null || true
elif command -v open &>/dev/null; then
    open http://localhost:8501 2>/dev/null || true
elif command -v start &>/dev/null; then
    start http://localhost:8501 2>/dev/null || true
fi

# Wait for background processes
wait "$UI_PID" "$API_PID"
