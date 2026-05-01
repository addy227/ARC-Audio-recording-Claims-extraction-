#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

# Dynamically find project root (assumes this script is in project root or a subfolder)
PROJECT_ROOT="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

echo "[INFO] Project root: $PROJECT_ROOT"
cd "$PROJECT_ROOT"

# Create virtual environment if it does not exist
VENV_DIR=".venv"
if [ ! -d "$VENV_DIR" ]; then
  echo "[INFO] Creating virtual environment in $VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi

# Activate virtual environment
source "$VENV_DIR/bin/activate"

# Upgrade pip
echo "[INFO] Upgrading pip"
pip install --upgrade pip

# Install dependencies if requirements.txt is present
if [ -f "requirements.txt" ]; then
  echo "[INFO] Installing dependencies from requirements.txt"
  pip install -r requirements.txt
else
  echo "[WARNING] requirements.txt not found, skipping dependency installation"
fi

# Optional: Export environment variables (edit this section as needed)
# export API_KEY="your-api-key"
# export ENV="production"

# Export PYTHONPATH for module discovery
export PYTHONPATH="$PROJECT_ROOT"
echo "[INFO] PYTHONPATH set to $PYTHONPATH"

echo "✅ Setup complete."
echo "To run the pipeline, use:"
echo ""
echo "    python main.py <optional_input_file>"
echo ""
