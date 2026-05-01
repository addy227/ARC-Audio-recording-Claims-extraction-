import os
import sys
import json
import time
import streamlit as st
import pandas as pd
from datetime import datetime
from streamlit_autorefresh import st_autorefresh

# Insert project root to sys.path so utils imports work
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from utils.util_master import load_pipeline_config, get_project_path

# ---- CONFIG ----
REFRESH_INTERVAL = 30  # seconds for auto-refresh

# ---- AUTO REFRESH ----
# Refresh page every REFRESH_INTERVAL seconds
st_autorefresh(interval=REFRESH_INTERVAL * 1000, key="auto_refresh_counter")

# ---- LOAD CONFIG AND METRICS DIRECTORY ----
config = load_pipeline_config()
paths = config.get("paths", {})
required_paths = ["metrics_dir"]
missing_paths = [p for p in required_paths if p not in paths]
if missing_paths:
    st.error(f"Missing required path configs: {missing_paths}")
    st.stop()

METRICS_DIR = get_project_path(paths["metrics_dir"])


# ---- FUNCTIONS ----
def load_all_metrics():
    metrics_list = []
    if not os.path.exists(METRICS_DIR):
        st.error(f"Metrics directory does not exist: {METRICS_DIR}")
        return metrics_list
    for file in sorted(os.listdir(METRICS_DIR)):
        if file.startswith("pipeline_metrics") and file.endswith(".json") and "latest" not in file:
            path = os.path.join(METRICS_DIR, file)
            try:
                with open(path, "r") as f:
                    data = json.load(f)
                    data["date"] = file.replace("pipeline_metrics_", "").replace(".json", "")
                    metrics_list.append(data)
            except Exception as e:
                st.warning(f"Failed to load metrics file {file}: {e}")
    return metrics_list


def flatten_metrics(metrics_list):
    rows = []
    for m in metrics_list:
        row = {
            "date": m["date"],
            "total_time_min": m.get("timings", {}).get("total_time_min"),
            "num_files": len(m.get("per_file", {})),
        }
        # Averages of per-file stage timings
        stage_averages = {}
        for file_data in m.get("per_file", {}).values():
            for key, value in file_data.items():
                if key.endswith("_min"):
                    stage_averages.setdefault(key, []).append(value)
        for stage, vals in stage_averages.items():
            row[f"avg_{stage}"] = round(sum(vals) / len(vals), 2)
        rows.append(row)
    return pd.DataFrame(rows)


def print_metrics_console(df):
    """Print metrics to console using logging."""
    from utils.logging_utils import get_logger
    logger = get_logger(__name__)
    logger.info("\n===== Pipeline Metrics Summary =====")
    logger.info(df.to_string(index=False))
    logger.info("====================================\n")


# ---- MAIN DASHBOARD ----

st.title("📊 Daily Pipeline Metrics Dashboard")

# Load metrics data
metrics_list = load_all_metrics()
if not metrics_list:
    st.warning("No metrics files found in the metrics directory.")
    st.stop()

df = flatten_metrics(metrics_list)
print_metrics_console(df)  # Print to console

# Show last updated timestamp
last_updated = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
st.caption(f"Last refreshed: {last_updated}")

# Manual refresh button
if st.button("🔄 Refresh Metrics"):
    st.experimental_rerun()

# Date selector dropdown
selected_date = st.selectbox(
    "Select a date to view details:", df["date"].sort_values(ascending=False)
)
selected_row = df[df["date"] == selected_date].iloc[0]

# Summary cards
col1, col2 = st.columns(2)
col1.metric("🕒 Total Pipeline Time (min)", selected_row["total_time_min"])
col2.metric("📁 Files Processed", selected_row["num_files"])

# Stage-wise average timings expander
with st.expander("📦 Stage-wise Average Times"):
    stage_cols = [c for c in df.columns if c.startswith("avg_")]
    if stage_cols:
        for col in stage_cols:
            stage_name = col.replace("avg_", "").replace("_min", "").replace("_", " ").title()
            st.write(f"**{stage_name}:** {selected_row[col]} min")
    else:
        st.write("No stage timing data available.")

# Historical metrics table
st.subheader("📈 Historical Metrics Overview")
st.dataframe(df.set_index("date").sort_index(ascending=False))

# Total time trend line chart
st.subheader("📉 Total Time Over Time")
st.line_chart(df.set_index("date")["total_time_min"])
