# Email Summary Report Setup

This script compiles log and metrics data from the pipeline and sends a formatted HTML email report.

## Setup Instructions

### 1. Configure Environment Variables

Add the following to your `.env` file (or set as environment variables):

```bash
# Email SMTP Configuration
EMAIL_SMTP_HOST=smtp.gmail.com          # Your SMTP server (Gmail, Outlook, etc.)
EMAIL_SMTP_PORT=587                     # SMTP port (587 for TLS, 465 for SSL)
EMAIL_SENDER=your-email@gmail.com       # Your email address
EMAIL_SENDER_PASSWORD=your-app-password # Your email password or app password
EMAIL_RECIPIENT=recipient1@email.com,recipient2@email.com  # Comma-separated recipients
EMAIL_USE_TLS=true                      # Use TLS (true/false)
```

### 2. Gmail Setup (if using Gmail)

If using Gmail, you need to:
1. Enable 2-Factor Authentication
2. Generate an App Password:
   - Go to Google Account → Security → 2-Step Verification → App passwords
   - Generate a password for "Mail"
   - Use this password as `EMAIL_SENDER_PASSWORD`

### 3. Other Email Providers

**Outlook/Hotmail:**
```bash
EMAIL_SMTP_HOST=smtp-mail.outlook.com
EMAIL_SMTP_PORT=587
EMAIL_USE_TLS=true
```

**Yahoo:**
```bash
EMAIL_SMTP_HOST=smtp.mail.yahoo.com
EMAIL_SMTP_PORT=587
EMAIL_USE_TLS=true
```

**Custom SMTP Server:**
```bash
EMAIL_SMTP_HOST=your-smtp-server.com
EMAIL_SMTP_PORT=587  # or 465 for SSL
EMAIL_USE_TLS=true   # false if using SSL on port 465
```

## Usage

### Run manually:
```bash
python scripts/utils/log_summary_emailer.py
```

### Schedule with cron (daily at 9 AM):
```bash
0 9 * * * cd /path/to/ARCall-Entity-Extractor && /path/to/.venv/bin/python scripts/utils/log_summary_emailer.py
```

### Schedule with systemd timer (Linux):
Create `/etc/systemd/system/pipeline-summary.timer`:
```ini
[Unit]
Description=Daily Pipeline Summary Email

[Timer]
OnCalendar=daily
OnCalendar=09:00
Persistent=true

[Install]
WantedBy=timers.target
```

Create `/etc/systemd/system/pipeline-summary.service`:
```ini
[Unit]
Description=Pipeline Summary Email Service

[Service]
Type=oneshot
User=your-user
WorkingDirectory=/path/to/ARCall-Entity-Extractor
ExecStart=/path/to/.venv/bin/python scripts/utils/log_summary_emailer.py
Environment="PATH=/path/to/.venv/bin:/usr/bin"
```

## Email Report Contents

The email includes:
- **Overall Statistics**: Total files processed, success/failure counts, success rate, processing times
- **Recent Pipeline Processing**: Last 20 files with status, processing time, stages, errors
- **Recent Claim Extraction**: Last 20 claim extraction results with status and errors
- **Summary Report Details**: Additional details from summary JSON files

## Troubleshooting

### Email not sending?
1. Check environment variables are set correctly
2. Verify SMTP credentials
3. Check firewall/network allows SMTP connections
4. For Gmail: Ensure app password is used (not regular password)

### No metrics found?
- Ensure pipeline has been run at least once
- Check that metrics files exist in `metrics/` directory
- Verify file permissions

### Import errors?
- Ensure you're running from project root
- Activate virtual environment
- Install required packages: `pip install python-dotenv`
