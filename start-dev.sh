#!/bin/bash
# TC Inventory Planner - Local Development Start Script
# Run from the project root directory

set -e

echo "═══════════════════════════════════════════════"
echo "  TC Inventory Planner - Starting..."
echo "═══════════════════════════════════════════════"

cd "$(dirname "$0")/backend"

# Load environment variables
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
    echo "✓ Loaded environment variables from .env"
else
    echo "⚠ No .env file found. Copy .env.template to .env and configure."
    exit 1
fi

# Check Python
if ! command -v python3 &>/dev/null; then
    echo "✗ Python 3 not found"
    exit 1
fi

# Create virtual environment if needed
if [ ! -d .venv ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi

source .venv/bin/activate
pip install -q -r requirements.txt

echo ""
echo "Starting FastAPI server on http://localhost:8000"
echo "API docs: http://localhost:8000/docs"
echo "═══════════════════════════════════════════════"
echo ""

uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
