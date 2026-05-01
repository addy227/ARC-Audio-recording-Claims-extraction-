"""Log Summary Emailer - Compiles log data, formats as table, and sends via email."""
import os, sys, csv, re, smtplib, socket
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from dotenv import load_dotenv

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))
from utils.config_loader import load_pipeline_config
from utils.util_master import get_project_path
from utils.logging_utils import get_logger

logger = get_logger(__name__)
load_dotenv()
config = load_pipeline_config()
paths = config.get("paths", {})
METRICS_DIR = Path(get_project_path(paths.get("metrics_dir", "metrics/")))
LOG_DIR = Path(get_project_path(paths.get("log_dir", "logs/")))

EMAIL_SMTP_HOST = os.getenv("EMAIL_SMTP_HOST", "")
EMAIL_SMTP_PORT = int(os.getenv("EMAIL_SMTP_PORT", "587"))
EMAIL_SENDER = os.getenv("EMAIL_SENDER", "")
EMAIL_SENDER_PASSWORD = os.getenv("EMAIL_SENDER_PASSWORD", "")
EMAIL_RECIPIENT = os.getenv("EMAIL_RECIPIENT", "")
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "true").lower() == "true"

def format_time(seconds: float) -> str:
    """Format seconds into human-readable time string."""
    return f"{seconds:.2f}s" if seconds < 60 else f"{seconds / 60:.2f}m" if seconds < 3600 else f"{seconds / 3600:.2f}h"

def read_today_logs() -> List[str]:
    """Read today's log file."""
    log_file = LOG_DIR / datetime.now().strftime("%Y-%m-%d") / "voiclaim.log"
    if not log_file.exists():
        logger.warning(f"Today's log file not found: {log_file}")
        return []
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
        logger.info(f"Read {len(lines)} log lines")
        return lines
    except Exception as e:
        logger.error(f"Failed to read log file: {e}", exc_info=True)
        return []

def parse_log_entry(line: str) -> Optional[Dict]:
    """Parse a single log line into structured data."""
    match = re.match(r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] \[(\w+)\] \[([^\]]+)\] (.+)', line.strip())
    return {"timestamp": match.group(1), "level": match.group(2), "logger": match.group(3), "message": match.group(4)} if match else None

def extract_file_processing_info(log_lines: List[str]) -> Dict[str, Dict]:
    """Extract processing information for each file from logs (only .mp3 files)."""
    file_info, current_file_base, file_id_map = {}, None, {}
    filename_patterns = [r'([a-f0-9-]{36}_\d+_\d+_\d+_\d+\.mp3)', r'([a-f0-9-]+_\d+_\d+_\d+_\d+\.mp3)', r'for:\s*([^\s]+\.mp3)', r'Processing\s+([^\s]+\.mp3)']
    
    for line in log_lines:
        entry = parse_log_entry(line)
        if not entry:
            continue
        message, level, timestamp = entry["message"], entry["level"], entry["timestamp"]
        
        # Extract filename (only .mp3)
        filename = next((m.group(1) for p in filename_patterns if (m := re.search(p, message, re.IGNORECASE))), None)
        
        # Map file ID to filename
        if (file_id_match := re.search(r'\[([a-f0-9-]{36})\]', message)):
            if filename:
                file_id_map[file_id_match.group(1)] = os.path.splitext(filename)[0]
            elif not filename:
                if (base_name := file_id_map.get(file_id_match.group(1))):
                    filename = f"{base_name}.mp3"
        
        # Only process .mp3 files
        if filename and filename.lower().endswith('.mp3'):
            current_file_base = os.path.splitext(filename)[0]
            if current_file_base not in file_info:
                file_info[current_file_base] = {"filename": filename, "stages": [], "failures": [], "api_status": [], "actions": [], "start_time": timestamp, "end_time": timestamp, "status": "unknown", "extraction_details": []}
        else:
            current_file_base = None
        
        # Process log entries for tracked files
        if current_file_base and current_file_base in file_info:
            info = file_info[current_file_base]
            info["end_time"] = timestamp
            msg_lower = message.lower()
            
            # Detect stages
            if "stage" in msg_lower and ("started" in msg_lower or any(f"stage {i}" in msg_lower for i in [1, 2, 3])):
                stage = (m.group(1).lower() if (m := re.search(r'stage\s*\d+:\s*(\w+)', message, re.IGNORECASE)) else None) or ("cleaning" if "cleaning" in msg_lower else "transcription" if "transcription" in msg_lower or "speech-to-text" in msg_lower else "extraction" if "extraction" in msg_lower or "claim" in msg_lower else None)
                if stage:
                    info["stages"].append({"stage": stage, "timestamp": timestamp, "status": "started"})
            
            # Detect completions
            if any(k in msg_lower for k in ["complete", "successfully", "✅", "succeeded"]):
                if info["stages"]:
                    info["stages"][-1]["status"] = "completed"
                if "successfully" in msg_lower or "✅" in message:
                    info["status"] = "success"
            
            # Detect failures
            if level in ["ERROR", "WARNING"] or any(k in msg_lower for k in ["failed", "❌"]):
                stage, reason = _extract_failure_info(msg_lower, message)
                info["failures"].append({"stage": stage, "reason": reason[:500], "timestamp": timestamp, "level": level})
                info["status"] = "failed"
            
            # Detect API status
            if any(k in msg_lower for k in ["api", "collect", "collectserviceapi"]):
                status = "connected" if any(k in msg_lower for k in ["connected", "success", "✅", "insert success"]) else "failed" if any(k in msg_lower for k in ["failed", "error", "❌", "timeout"]) else "attempted"
                info["api_status"].append({"status": status, "timestamp": timestamp, "details": message[:300]})
            
            # Capture extraction details and actions
            if any(k in msg_lower for k in ["extraction", "claim"]) and "extracted" in msg_lower:
                info["extraction_details"].append({"detail": message[:300], "timestamp": timestamp})
            if any(k in msg_lower for k in ["upload", "saved", "moved", "inserted", "retry", "sent", "attached"]):
                info["actions"].append({"action": message[:300], "timestamp": timestamp})
    
    return file_info

def _extract_failure_info(msg_lower: str, message: str) -> tuple:
    """Extract failure stage and reason from message."""
    failure_map = {
        ("cleaning", "audio"): ("Audio Cleaning", "Audio file could not be processed/cleaned" if "couldn't" in msg_lower or "could not" in msg_lower else message),
        ("transcription", "speech-to-text", "stt"): ("Transcription", "Failed to convert audio to text" if "couldn't" in msg_lower or "could not" in msg_lower else message),
        ("extraction", "claim"): ("Claim Extraction", _get_extraction_reason(msg_lower, message)),
        ("api", "collect"): ("API Integration", "Failed to connect to API" if "connect" in msg_lower else "API request timeout" if "timeout" in msg_lower else f"API call failed: {message[:300]}"),
        ("upload", "blob"): ("File Upload", f"File upload failed: {message[:300]}")
    }
    for keywords, result in failure_map.items():
        if any(k in msg_lower for k in keywords):
            return result
    return "Pipeline Processing", message

def _get_extraction_reason(msg_lower: str, message: str) -> str:
    """Get extraction failure reason."""
    if "no valid claims" in msg_lower or "no claims extracted" in msg_lower:
        return "No valid claims found in transcript"
    elif "json" in msg_lower or "parse" in msg_lower:
        return "Failed to parse model output/JSON"
    elif "model" in msg_lower or "llm" in msg_lower:
        return "Model processing error"
    elif "validation" in msg_lower:
        return "Data validation failed"
    return f"Extraction failed: {message[:300]}"

def calculate_stats_from_logs(file_info: Dict[str, Dict]) -> Dict:
    """Calculate statistics from log-based file information."""
    stats = {"total_files_processed": len(file_info), "successful_files": sum(1 for info in file_info.values() if info["status"] == "success"), "failed_files": sum(1 for info in file_info.values() if info["status"] == "failed"), "total_processing_time_sec": 0.0, "avg_processing_time_sec": 0.0, "success_rate": 0.0}
    if not file_info:
        return stats
    for info in file_info.values():
        try:
            elapsed = (datetime.strptime(info["end_time"], "%Y-%m-%d %H:%M:%S") - datetime.strptime(info["start_time"], "%Y-%m-%d %H:%M:%S")).total_seconds()
            if elapsed > 0:
                stats["total_processing_time_sec"] += elapsed
        except (ValueError, TypeError):
            pass
    if stats["total_files_processed"] > 0:
        stats["avg_processing_time_sec"] = stats["total_processing_time_sec"] / stats["total_files_processed"]
        stats["success_rate"] = (stats["successful_files"] / stats["total_files_processed"]) * 100
    return stats

def compile_processing_details_csv() -> Optional[Path]:
    """Compile processing details from logs to CSV."""
    try:
        csv_path = METRICS_DIR / f"processing_details_{datetime.now().strftime('%Y-%m-%d')}.csv"
        log_lines = read_today_logs()
        if not log_lines:
            return None
        file_info = extract_file_processing_info(log_lines)
        if not file_info:
            return None
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["File Name", "Status", "Stages Completed", "Failure Stage", "Failure Reason", "API Connection Status", "API Details", "Extraction Details", "Actions Taken", "Error Level"])
            for info in file_info.values():
                stages_completed = ", ".join([s["stage"] for s in info["stages"] if s.get("status") == "completed"]) or "None"
                if info["failures"]:
                    failure_stage = "; ".join(set([f["stage"] for f in info["failures"]])) or "N/A"
                    failure_reason = " | ".join([f["reason"] for f in info["failures"]])
                    error_level = "ERROR" if "ERROR" in [f["level"] for f in info["failures"]] else "WARNING" if "WARNING" in [f["level"] for f in info["failures"]] else info["failures"][0]["level"] if info["failures"] else "N/A"
                else:
                    failure_stage = failure_reason = error_level = "N/A"
                api_status = api_details = "N/A"
                if info["api_status"]:
                    latest = info["api_status"][-1]
                    api_status, api_details = latest["status"], latest["details"]
                extraction_details = "; ".join([d["detail"] for d in info.get("extraction_details", [])[:3]]) or "N/A"
                actions_taken = "; ".join([a["action"] for a in info["actions"][:5]]) or "None"
                writer.writerow([info["filename"], info["status"], stages_completed, failure_stage, failure_reason[:1000], api_status, api_details[:300], extraction_details[:400], actions_taken[:500], error_level])
        logger.info(f"✅ Compiled processing details for {len(file_info)} files")
        return csv_path
    except Exception as e:
        logger.error(f"❌ Failed to compile processing details: {e}", exc_info=True)
        return None

def create_summary_html(stats: Dict) -> str:
    """Create HTML summary with statistics table."""
    return f"""<!DOCTYPE html><html><head><style>body{{font-family:'Segoe UI',Tahoma,Geneva,Verdana,sans-serif;margin:0;padding:20px;background-color:#f5f5f5}}.container{{max-width:800px;margin:0 auto;background-color:white;padding:30px;border-radius:10px;box-shadow:0 2px 10px rgba(0,0,0,0.1)}}h1{{color:#2c3e50;margin-bottom:10px;border-bottom:3px solid #3498db;padding-bottom:10px}}.header-info{{color:#7f8c8d;font-size:14px;margin-bottom:30px}}.info-section{{background-color:#ecf0f1;padding:20px;border-radius:8px;margin:20px 0}}.stats-table{{width:100%;border-collapse:collapse;margin:20px 0;background-color:white;border-radius:8px;overflow:hidden;box-shadow:0 2px 5px rgba(0,0,0,0.1)}}.stats-table th{{background-color:#3498db;color:white;padding:15px;text-align:left;font-weight:600;font-size:14px;text-transform:uppercase;letter-spacing:0.5px}}.stats-table td{{padding:12px 15px;border-bottom:1px solid #ecf0f1;color:#2c3e50}}.stats-table tr:last-child td{{border-bottom:none}}.stats-table tr:hover{{background-color:#f8f9fa}}.stat-label{{font-weight:600;color:#34495e}}.success-badge{{background-color:#27ae60;color:white;padding:5px 15px;border-radius:20px;display:inline-block;font-size:12px;font-weight:bold}}.failed-badge{{background-color:#e74c3c;color:white;padding:5px 15px;border-radius:20px;display:inline-block;font-size:12px;font-weight:bold}}.attachment-note{{background-color:#fff3cd;border-left:4px solid #ffc107;padding:15px;margin:20px 0;border-radius:4px}}.footer{{text-align:center;color:#95a5a6;font-size:12px;margin-top:30px;padding-top:20px;border-top:1px solid #ecf0f1}}</style></head><body><div class="container"><h1>📊 Pipeline Processing Summary Report</h1><div class="header-info"><strong>Generated:</strong> {datetime.now().strftime("%B %d, %Y at %I:%M %p")}</div><div class="info-section"><h2>📋 Today's Statistics</h2><table class="stats-table"><thead><tr><th>Metric</th><th>Value</th></tr></thead><tbody><tr><td class="stat-label">Total Files Processed</td><td>{stats["total_files_processed"]}</td></tr><tr><td class="stat-label">Successful</td><td><span class="success-badge">{stats["successful_files"]}</span></td></tr><tr><td class="stat-label">Failed</td><td><span class="failed-badge">{stats["failed_files"]}</span></td></tr><tr><td class="stat-label">Success Rate</td><td><strong>{stats["success_rate"]:.1f}%</strong></td></tr><tr><td class="stat-label">Average Processing Time</td><td>{format_time(stats["avg_processing_time_sec"])}</td></tr><tr><td class="stat-label">Total Processing Time</td><td>{format_time(stats["total_processing_time_sec"])}</td></tr></tbody></table></div><div class="attachment-note"><strong>📎 Processing Details Attached:</strong> A CSV file containing comprehensive processing details for all files processed today has been attached to this email.</div><div class="footer"><p>This is an automated report generated by the ARCall Entity Extractor Pipeline.</p><p>For detailed metrics, please refer to the attached CSV file.</p></div></div></body></html>"""

def send_email(subject: str, html_body: str, recipients: List[str], attachment_path: Optional[Path] = None) -> bool:
    """Send email with HTML content."""
    if not all([EMAIL_SMTP_HOST, EMAIL_SENDER, EMAIL_SENDER_PASSWORD, recipients]):
        logger.error("Email configuration incomplete")
        return False
    try:
        try:
            socket.gethostbyname(EMAIL_SMTP_HOST)
            logger.info(f"✅ DNS resolution successful for {EMAIL_SMTP_HOST}")
        except socket.gaierror as e:
            logger.error(f"❌ DNS resolution failed: {e}")
            return False
        msg = MIMEMultipart('alternative')
        msg['From'], msg['To'], msg['Subject'] = EMAIL_SENDER, ", ".join(recipients), subject
        msg.attach(MIMEText(html_body, 'html'))
        if attachment_path and attachment_path.exists():
            try:
                with open(attachment_path, "rb") as f:
                    attachment = MIMEBase('application', 'octet-stream')
                    attachment.set_payload(f.read())
                    encoders.encode_base64(attachment)
                    attachment.add_header('Content-Disposition', f'attachment; filename= {attachment_path.name}')
                    msg.attach(attachment)
                logger.info(f"✅ Attached CSV: {attachment_path.name}")
            except Exception as e:
                logger.warning(f"⚠️ Failed to attach CSV: {e}")
        logger.info(f"Connecting to {EMAIL_SMTP_HOST}:{EMAIL_SMTP_PORT}")
        server = smtplib.SMTP(EMAIL_SMTP_HOST, EMAIL_SMTP_PORT, timeout=30)
        if EMAIL_USE_TLS:
            server.starttls()
        server.login(EMAIL_SENDER, EMAIL_SENDER_PASSWORD)
        server.sendmail(EMAIL_SENDER, recipients, msg.as_string())
        server.quit()
        logger.info("✅ Email sent successfully")
        return True
    except (socket.gaierror, smtplib.SMTPAuthenticationError, smtplib.SMTPConnectError) as e:
        logger.error(f"❌ Email error: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ Failed to send email: {e}", exc_info=True)
        return False

def save_html_to_file(html_content: str) -> Path:
    """Save HTML content to file."""
    output_path = METRICS_DIR / f"pipeline_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    logger.info(f"✅ HTML report saved to: {output_path}")
    return output_path

def main():
    """Main function to compile and send log summary."""
    logger.info("📊 Starting log summary compilation...")
    log_lines = read_today_logs()
    file_info = extract_file_processing_info(log_lines) if log_lines else {}
    stats = calculate_stats_from_logs(file_info) if file_info else {"total_files_processed": 0, "successful_files": 0, "failed_files": 0, "total_processing_time_sec": 0.0, "avg_processing_time_sec": 0.0, "success_rate": 0.0}
    logger.info(f"📊 Statistics: {stats['total_files_processed']} files processed")
    csv_path = compile_processing_details_csv()
    html_content = create_summary_html(stats)
    if not EMAIL_SMTP_HOST or not EMAIL_SENDER or not EMAIL_RECIPIENT:
        logger.warning("⚠️ Email not configured. Saving HTML report to file.")
        save_html_to_file(html_content)
        if csv_path:
            logger.info(f"📊 CSV saved to: {csv_path}")
        return True
    recipients = [email.strip() for email in EMAIL_RECIPIENT.split(",") if email.strip()]
    if not recipients:
        logger.warning("⚠️ No recipients. Saving HTML report to file.")
        save_html_to_file(html_content)
        return True
    subject = f"Pipeline Processing Summary - {datetime.now().strftime('%Y-%m-%d')}"
    success = send_email(subject, html_content, recipients, csv_path)
    if not success:
        logger.warning("⚠️ Email sending failed. Saving HTML report as backup.")
        save_html_to_file(html_content)
        if csv_path:
            logger.info(f"📊 CSV saved to: {csv_path}")
    return success

if __name__ == "__main__":
    try:
        success = main()
        sys.exit(0 if success else 1)
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        sys.exit(2)
