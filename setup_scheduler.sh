#!/bin/bash
#
# Setup script for Voiclaim Pipeline Scheduler
# This script installs and configures the systemd service and timer
# Can be run with or without sudo (uses user systemd services if no sudo)
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_FILE="$SCRIPT_DIR/voiclaim-scheduler.service"
TIMER_FILE="$SCRIPT_DIR/voiclaim-scheduler.timer"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "=========================================="
echo "Voiclaim Pipeline Scheduler Setup"
echo "=========================================="
echo ""

# Determine if running with sudo or as regular user
USE_USER_SYSTEMD=false
if [ "$EUID" -ne 0 ]; then 
    USE_USER_SYSTEMD=true
    echo -e "${GREEN}Running in user mode (no sudo required)${NC}"
    CURRENT_USER=$USER
    SYSTEMD_DIR="$HOME/.config/systemd/user"
    SYSTEMD_SERVICE="$SYSTEMD_DIR/voiclaim-scheduler.service"
    SYSTEMD_TIMER="$SYSTEMD_DIR/voiclaim-scheduler.timer"
    SYSTEMCTL_CMD="systemctl --user"
else
    echo -e "${GREEN}Running in system mode (with sudo)${NC}"
CURRENT_USER=${SUDO_USER:-$USER}
    SYSTEMD_DIR="/etc/systemd/system"
    SYSTEMD_SERVICE="$SYSTEMD_DIR/voiclaim-scheduler.service"
    SYSTEMD_TIMER="$SYSTEMD_DIR/voiclaim-scheduler.timer"
    SYSTEMCTL_CMD="systemctl"
fi

echo -e "${GREEN}Detected user: $CURRENT_USER${NC}"

# Get project root (script directory)
PROJECT_ROOT="$SCRIPT_DIR"
echo -e "${GREEN}Detected project root: $PROJECT_ROOT${NC}"

# Get Python path - prioritize virtual environment
# Check for specific venv path first
SPECIFIC_VENV="/home/nalabotalaadvait/Documents/Dev1/ARCall-Entity-Extractor/.venv/bin/python3"
if [ -f "$SPECIFIC_VENV" ]; then
    PYTHON_PATH="$SPECIFIC_VENV"
    echo -e "${GREEN}Using specified virtual environment Python: $PYTHON_PATH${NC}"
elif [ -f "$PROJECT_ROOT/.venv/bin/python3" ]; then
    PYTHON_PATH="$PROJECT_ROOT/.venv/bin/python3"
    echo -e "${GREEN}Using project virtual environment Python: $PYTHON_PATH${NC}"
elif [ -f "$PROJECT_ROOT/venv/bin/python3" ]; then
    PYTHON_PATH="$PROJECT_ROOT/venv/bin/python3"
    echo -e "${GREEN}Using project virtual environment Python: $PYTHON_PATH${NC}"
elif [ -n "$VIRTUAL_ENV" ] && [ -f "$VIRTUAL_ENV/bin/python3" ]; then
    PYTHON_PATH="$VIRTUAL_ENV/bin/python3"
    echo -e "${GREEN}Using active virtual environment Python: $PYTHON_PATH${NC}"
else
    PYTHON_PATH=$(which python3)
    if [ -z "$PYTHON_PATH" ]; then
        PYTHON_PATH="/usr/bin/python3"
        echo -e "${YELLOW}Warning: python3 not found in PATH, using default: $PYTHON_PATH${NC}"
        echo -e "${YELLOW}Note: Virtual environment not found. Make sure dependencies are installed system-wide.${NC}"
    else
        echo -e "${YELLOW}Warning: No virtual environment detected, using system Python: $PYTHON_PATH${NC}"
        echo -e "${YELLOW}Note: Make sure all dependencies are installed for this Python interpreter.${NC}"
    fi
fi

# Create a temporary service file with updated paths
TEMP_SERVICE_FILE=$(mktemp)
cp "$SERVICE_FILE" "$TEMP_SERVICE_FILE"

# Update service file with project root and Python path
echo "Updating service file with:"
echo "  - WorkingDirectory: $PROJECT_ROOT"
echo "  - ExecStart: $PYTHON_PATH $PROJECT_ROOT/scheduler.py"

sed -i "s|WorkingDirectory=.*|WorkingDirectory=$PROJECT_ROOT|g" "$TEMP_SERVICE_FILE"
sed -i "s|ExecStart=.*|ExecStart=$PYTHON_PATH $PROJECT_ROOT/scheduler.py|g" "$TEMP_SERVICE_FILE"

# For user systemd, remove User= line (not needed for user services)
if [ "$USE_USER_SYSTEMD" = true ]; then
    echo "  - Removing User= line (not needed for user services)"
    sed -i "/^User=/d" "$TEMP_SERVICE_FILE"
else
    echo "  - User: $CURRENT_USER"
    sed -i "s|User=%i|User=$CURRENT_USER|g" "$TEMP_SERVICE_FILE"
fi

# Verify files exist
if [ ! -f "$SERVICE_FILE" ]; then
    echo -e "${RED}Error: Service file not found: $SERVICE_FILE${NC}"
    exit 1
fi

if [ ! -f "$TIMER_FILE" ]; then
    echo -e "${RED}Error: Timer file not found: $TIMER_FILE${NC}"
    exit 1
fi

# Create systemd directory if it doesn't exist
echo "Creating systemd directory: $SYSTEMD_DIR"
mkdir -p "$SYSTEMD_DIR"

# Copy files to systemd directory
echo "Copying service and timer files to $SYSTEMD_DIR/..."
cp "$TEMP_SERVICE_FILE" "$SYSTEMD_SERVICE"
cp "$TIMER_FILE" "$SYSTEMD_TIMER"

# Clean up temporary file
rm "$TEMP_SERVICE_FILE"

# Reload systemd
echo "Reloading systemd daemon..."
$SYSTEMCTL_CMD daemon-reload

# Enable timer (so it starts on boot/login)
echo "Enabling timer (will start on boot/login)..."
$SYSTEMCTL_CMD enable voiclaim-scheduler.timer

# For user systemd, enable lingering so it runs even when not logged in
if [ "$USE_USER_SYSTEMD" = true ]; then
    echo "Enabling systemd user lingering (allows services to run when not logged in)..."
    loginctl enable-linger "$CURRENT_USER" 2>/dev/null || echo -e "${YELLOW}Note: loginctl may require sudo for enable-linger${NC}"
fi

# Start timer
echo "Starting timer..."
$SYSTEMCTL_CMD start voiclaim-scheduler.timer

# Show status
echo ""
echo "=========================================="
echo "Setup Complete!"
echo "=========================================="
echo ""
echo -e "${GREEN}Timer Status:${NC}"
$SYSTEMCTL_CMD status voiclaim-scheduler.timer --no-pager -l || true

echo ""
echo -e "${GREEN}Next scheduled run:${NC}"
$SYSTEMCTL_CMD list-timers voiclaim-scheduler.timer --no-pager || true

echo ""
echo -e "${YELLOW}Useful commands:${NC}"
if [ "$USE_USER_SYSTEMD" = true ]; then
    echo "  Check timer status:  systemctl --user status voiclaim-scheduler.timer"
    echo "  View logs:           journalctl --user -u voiclaim-scheduler.service -f"
    echo "  Manual trigger:      systemctl --user start voiclaim-scheduler.service"
    echo "  Stop timer:          systemctl --user stop voiclaim-scheduler.timer"
    echo ""
    echo -e "${YELLOW}Note:${NC} User systemd services run even when you're not logged in"
    echo "      (if lingering is enabled)"
else
echo "  Check timer status:  sudo systemctl status voiclaim-scheduler.timer"
echo "  View logs:           sudo journalctl -u voiclaim-scheduler.service -f"
echo "  Manual trigger:      sudo systemctl start voiclaim-scheduler.service"
echo "  Stop timer:          sudo systemctl stop voiclaim-scheduler.timer"
fi
echo ""
echo -e "${GREEN}Setup completed successfully!${NC}"
