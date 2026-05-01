# Voiclaim Pipeline Scheduler Setup Guide

This guide explains how to set up the automated daily scheduler for the Voiclaim pipeline.

## Overview

The scheduler runs two scripts sequentially every day at 10:00 AM:
1. **main.py** - Processes audio files (default: yesterday's files)
2. **app.py** - Sends extracted claims to the API

## Files Created

- `scheduler.py` - Main scheduler script that runs main.py then app.py
- `catchup_processor.py` - Script to process historical dates (Jan 1st to today)
- `voiclaim-scheduler.service` - Systemd service file
- `voiclaim-scheduler.timer` - Systemd timer file (runs daily at 10 AM)

## Setup Instructions

### Step 1: Update Service File (Optional)

**Note:** The setup script (`setup_scheduler.sh`) automatically updates the service file with correct paths. You only need to manually edit if you want to add environment variables.

If you need to add environment variables, edit `voiclaim-scheduler.service` and add them under the `[Service]` section:

```ini
[Service]
...
Environment="POST_PROCESS_URL=https://your-api-url.com"
Environment="API_KEY=your-api-key"
```

The setup script will automatically:
- Detect your username
- Set the correct WorkingDirectory (project root)
- Set the correct ExecStart path with Python path
- Remove User= line for user systemd services (not needed)

### Step 2: Install Systemd Service and Timer

**Option A: Automated Setup (Recommended)**

```bash
# Run the setup script (works with or without sudo)
./setup_scheduler.sh

# If you have sudo access, you can use:
# sudo ./setup_scheduler.sh
```

The script will automatically:
- Detect if you're running with or without sudo
- Use user systemd services (no sudo) or system-wide services (with sudo)
- Configure paths and settings automatically
- Enable and start the timer

**Option B: Manual Setup**

If you prefer manual setup:

**Without sudo (user systemd services):**
```bash
# Create user systemd directory
mkdir -p ~/.config/systemd/user

# Copy files
cp voiclaim-scheduler.service ~/.config/systemd/user/
cp voiclaim-scheduler.timer ~/.config/systemd/user/

# Edit service file to update paths
nano ~/.config/systemd/user/voiclaim-scheduler.service

# Reload and enable
systemctl --user daemon-reload
systemctl --user enable voiclaim-scheduler.timer
systemctl --user start voiclaim-scheduler.timer

# Enable lingering (so it runs when not logged in)
loginctl enable-linger $USER
```

**With sudo (system-wide services):**
```bash
# Copy service and timer files to systemd directory
sudo cp voiclaim-scheduler.service /etc/systemd/system/
sudo cp voiclaim-scheduler.timer /etc/systemd/system/

# Reload systemd to recognize new files
sudo systemctl daemon-reload

# Enable the timer (so it starts on boot)
sudo systemctl enable voiclaim-scheduler.timer

# Start the timer
sudo systemctl start voiclaim-scheduler.timer

# Verify timer is active
sudo systemctl status voiclaim-scheduler.timer
```

### Step 3: Verify Setup

**If using user systemd (no sudo):**
```bash
# Check timer status
systemctl --user status voiclaim-scheduler.timer

# List all timers
systemctl --user list-timers --all | grep voiclaim

# View next scheduled run time
systemctl --user list-timers voiclaim-scheduler.timer
```

**If using system-wide services (with sudo):**
```bash
# Check timer status
sudo systemctl status voiclaim-scheduler.timer

# List all timers
systemctl list-timers --all | grep voiclaim

# View next scheduled run time
sudo systemctl list-timers voiclaim-scheduler.timer
```

### Step 4: Test the Scheduler Manually

Before relying on the timer, test the scheduler manually:

```bash
# Test scheduler script directly
python3 scheduler.py

# Or test via systemd service
# For user systemd:
systemctl --user start voiclaim-scheduler.service
systemctl --user status voiclaim-scheduler.service

# For system-wide services:
sudo systemctl start voiclaim-scheduler.service
sudo systemctl status voiclaim-scheduler.service
```

## Catch-up Processing (Jan 1st to Today)

To process files from January 1st to today:

```bash
# Process from Jan 1st to today
python3 catchup_processor.py --start-date 2026-01-01

# Process specific date range
python3 catchup_processor.py --start-date 2026-01-01 --end-date 2026-01-10

# Process with more workers (faster)
python3 catchup_processor.py --start-date 2026-01-01 --max-workers 8

# Skip API integration during catch-up (process only)
python3 catchup_processor.py --start-date 2026-01-01 --skip-api

# Run API integration only once at the end (instead of after each day)
python3 catchup_processor.py --start-date 2026-01-01 --run-api-once
```

## Monitoring

### View Logs

**For user systemd (no sudo):**
```bash
# View scheduler service logs
journalctl --user -u voiclaim-scheduler.service -f

# View recent logs
journalctl --user -u voiclaim-scheduler.service -n 100

# View logs for a specific date
journalctl --user -u voiclaim-scheduler.service --since "2026-01-20" --until "2026-01-21"
```

**For system-wide services (with sudo):**
```bash
# View scheduler service logs
sudo journalctl -u voiclaim-scheduler.service -f

# View recent logs
sudo journalctl -u voiclaim-scheduler.service -n 100

# View logs for a specific date
sudo journalctl -u voiclaim-scheduler.service --since "2026-01-20" --until "2026-01-21"
```

### Check Timer Status

**For user systemd (no sudo):**
```bash
# Check if timer is active
systemctl --user is-active voiclaim-scheduler.timer

# Check when timer will run next
systemctl --user list-timers voiclaim-scheduler.timer
```

**For system-wide services (with sudo):**
```bash
# Check if timer is active
sudo systemctl is-active voiclaim-scheduler.timer

# Check when timer will run next
sudo systemctl list-timers voiclaim-scheduler.timer
```

## Management Commands

### Start/Stop Timer

**For user systemd (no sudo):**
```bash
# Stop the timer (won't run scheduled jobs)
systemctl --user stop voiclaim-scheduler.timer

# Start the timer
systemctl --user start voiclaim-scheduler.timer

# Disable timer (won't start on login)
systemctl --user disable voiclaim-scheduler.timer

# Enable timer (will start on login)
systemctl --user enable voiclaim-scheduler.timer
```

**For system-wide services (with sudo):**
```bash
# Stop the timer (won't run scheduled jobs)
sudo systemctl stop voiclaim-scheduler.timer

# Start the timer
sudo systemctl start voiclaim-scheduler.timer

# Disable timer (won't start on boot)
sudo systemctl disable voiclaim-scheduler.timer

# Enable timer (will start on boot)
sudo systemctl enable voiclaim-scheduler.timer
```

### Manual Trigger

**For user systemd (no sudo):**
```bash
# Manually trigger the service (runs immediately)
systemctl --user start voiclaim-scheduler.service
```

**For system-wide services (with sudo):**
```bash
# Manually trigger the service (runs immediately)
sudo systemctl start voiclaim-scheduler.service
```

## Troubleshooting

### Timer Not Running

**For user systemd (no sudo):**
1. Check if timer is enabled:
   ```bash
   systemctl --user is-enabled voiclaim-scheduler.timer
   ```

2. Check timer status:
   ```bash
   systemctl --user status voiclaim-scheduler.timer
   ```

3. Check service logs:
   ```bash
   journalctl --user -u voiclaim-scheduler.service -n 50
   ```

**For system-wide services (with sudo):**
1. Check if timer is enabled:
   ```bash
   sudo systemctl is-enabled voiclaim-scheduler.timer
   ```

2. Check timer status:
   ```bash
   sudo systemctl status voiclaim-scheduler.timer
   ```

3. Check service logs:
   ```bash
   sudo journalctl -u voiclaim-scheduler.service -n 50
   ```

### Service Fails

1. Check Python path:
   ```bash
   which python3
   ```

2. Test scheduler script manually:
   ```bash
   python3 scheduler.py
   ```

3. Check file permissions:
   ```bash
   ls -l scheduler.py main.py app.py
   ```

### After System Restart

The timer is configured with `Persistent=true`, which means:
- If the system was off during the scheduled time (10 AM), it will run immediately after boot
- You only need to manually start it if you disable it or if there's an issue

To verify it's set to auto-start:

**For user systemd (no sudo):**
```bash
systemctl --user is-enabled voiclaim-scheduler.timer
```

**For system-wide services (with sudo):**
```bash
sudo systemctl is-enabled voiclaim-scheduler.timer
```

## Configuration

### Change Schedule Time

Edit `voiclaim-scheduler.timer` and modify the `OnCalendar` line:

```ini
# Run at 10:00 AM daily (current)
OnCalendar=*-*-* 10:00:00

# Run at 2:00 PM daily
OnCalendar=*-*-* 14:00:00

# Run at 10:00 AM on weekdays only
OnCalendar=Mon..Fri 10:00:00

# Run twice daily (10 AM and 6 PM)
OnCalendar=*-*-* 10:00:00
OnCalendar=*-*-* 18:00:00
```

After editing, reload:

**For user systemd (no sudo):**
```bash
systemctl --user daemon-reload
systemctl --user restart voiclaim-scheduler.timer
```

**For system-wide services (with sudo):**
```bash
sudo systemctl daemon-reload
sudo systemctl restart voiclaim-scheduler.timer
```

### Adjust Scheduler Parameters

Edit `scheduler.py` to change:
- `--day` parameter (default: 1 for yesterday)
- `--max-workers` (default: 4)
- `--cleanup-days` (default: 5)

## Notes

- The scheduler processes **yesterday's files** by default (`--day 1`)
- If `main.py` fails, `app.py` will NOT run (fail-safe)
- All logs are written to both journald and the application log files
- The timer has a 300-second randomized delay to avoid system load spikes
