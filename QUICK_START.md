# Quick Start Guide - Voiclaim Scheduler

## 🚀 Quick Setup (One Command)

```bash
sudo ./setup_scheduler.sh
```

This will:
- Install the systemd service and timer
- Configure it to run daily at 10:00 AM
- Enable auto-start on system reboot

## 📋 Process Files from Jan 1st to Today

```bash
# Process all files from Jan 1st to today
python3 catchup_processor.py --start-date 2026-01-01

# This will:
# 1. Process each day sequentially
# 2. Run app.py after each day (to send claims to API)
# 3. Show progress and summary
```

## ✅ Verify Setup

```bash
# Check if timer is active
sudo systemctl status voiclaim-scheduler.timer

# See when it will run next
sudo systemctl list-timers voiclaim-scheduler.timer

# View logs
sudo journalctl -u voiclaim-scheduler.service -f
```

## 🔧 Manual Operations

```bash
# Manually trigger the pipeline (runs immediately)
sudo systemctl start voiclaim-scheduler.service

# Stop the scheduled runs
sudo systemctl stop voiclaim-scheduler.timer

# Start the scheduled runs
sudo systemctl start voiclaim-scheduler.timer
```

## 📝 What Gets Scheduled?

**Daily at 10:00 AM:**
1. Runs `main.py` with `--day 1` (processes yesterday's audio files)
2. After main.py succeeds, runs `app.py` (sends claims to API)
3. If main.py fails, app.py does NOT run (fail-safe)

## 📚 More Details

See `SCHEDULER_SETUP.md` for complete documentation.
