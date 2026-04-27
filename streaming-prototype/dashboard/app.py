"""
Streamlit HITL dashboard — Bayesian Sensor Monitoring.

Reads alerts and scoring_log from the shared DuckDB file.
Operator clicks one of 4 override buttons per pending alert;
the scorer's background thread picks up the decision and applies
the corresponding effect to the in-memory NIG prior.
"""

import os
import time
from datetime import datetime, timezone

import duckdb
import plotly.graph_objects as go
import streamlit as st

DB_PATH = os.getenv("DB_PATH", "./sensor.duckdb")

OVERRIDE_OPTIONS = [
    "IGNORE_ANOMALY",
    "ACKNOWLEDGED_OUTLIER",
    "REGIME_CHANGE",
    "TRANSITION_STATE",
]

LEVEL_COLOR = {"RED": "#ff4444", "YELLOW": "#ffaa00"}

st.set_page_config(page_title="Bayesian Sensor HITL", layout="wide")
st.title("Bayesian Sensor Monitoring — HITL Dashboard")


# ── DB helpers ────────────────────────────────────────────────────────────────

def db_read(sql: str, params: tuple = ()):
    """Read-only connection — safe to open concurrently with the scorer writer."""
    try:
        conn = duckdb.connect(DB_PATH, read_only=True)
        df   = conn.execute(sql, list(params)).df()
        conn.close()
        return df
    except Exception:
        return None


def db_write(sql: str, params: tuple = ()) -> bool:
    """Short-lived write connection with retry for WAL serialisation."""
    for attempt in range(5):
        try:
            conn = duckdb.connect(DB_PATH)
            conn.execute(sql, list(params))
            conn.commit()
            conn.close()
            return True
        except Exception as exc:
            if attempt == 4:
                st.error(f"DB write failed: {exc}")
            time.sleep(0.2)
    return False


def apply_override(alert_id: int, status: str) -> None:
    db_write("""
        UPDATE alerts
        SET    override_status = ?,
               override_by    = 'operator',
               override_ts    = ?
        WHERE  id = ?
    """, (status, datetime.now(timezone.utc).isoformat(), alert_id))


# ── Section 1: Pending Alerts ─────────────────────────────────────────────────

st.subheader("Pending Alerts")

pending_df = db_read("""
    SELECT id, ts, device_id, sensor_type, raw_value, score, level
    FROM   alerts
    WHERE  override_status = 'PENDING'
    ORDER  BY
        CASE level WHEN 'RED' THEN 0 ELSE 1 END,
        score DESC
""")

if pending_df is None or pending_df.empty:
    st.success("No pending alerts.")
else:
    st.caption(f"{len(pending_df)} alert(s) awaiting review — RED first, then YELLOW by score")

    for _, row in pending_df.iterrows():
        color = LEVEL_COLOR.get(row["level"], "#888888")
        st.markdown(
            f"<div style='border-left:4px solid {color}; padding:6px 12px; "
            f"margin-bottom:6px; background:#1a1a1a; border-radius:3px;'>"
            f"<strong>{row['device_id']}</strong> / {row['sensor_type']}"
            f" &nbsp;|&nbsp; Score: <strong>{row['score']:.3f}</strong>"
            f" &nbsp;|&nbsp; Level: <strong style='color:{color}'>{row['level']}</strong>"
            f" &nbsp;|&nbsp; Value: {row['raw_value']:.4f}"
            f" &nbsp;|&nbsp; <span style='color:#888'>{str(row['ts'])[:19]}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
        cols = st.columns(4)
        for i, status in enumerate(OVERRIDE_OPTIONS):
            if cols[i].button(status, key=f"{row['id']}_{status}", use_container_width=True):
                apply_override(int(row["id"]), status)
                st.rerun()

# ── Section 2: Device Health Trends ──────────────────────────────────────────

st.divider()
st.subheader("Device Health Trends")

device_sel = st.selectbox("Select device", ["pump-042", "pump-099"])

trend_df = db_read("""
    SELECT ts, sensor_type, score
    FROM   scoring_log
    WHERE  device_id = ?
    ORDER  BY ts DESC
    LIMIT  200
""", (device_sel,))

if trend_df is not None and not trend_df.empty:
    trend_df = trend_df.sort_values("ts")
    fig = go.Figure()
    for stype, color in [("vibration", "#1e90ff"), ("temperature", "#ff8c00")]:
        sub = trend_df[trend_df["sensor_type"] == stype]
        if not sub.empty:
            fig.add_trace(go.Scatter(
                x=sub["ts"], y=sub["score"],
                name=stype, line=dict(color=color),
                mode="lines+markers", marker=dict(size=3),
            ))
    fig.add_hline(y=3.0, line_dash="dash", line_color="#ffaa00",
                  annotation_text="YELLOW (3.0)")
    fig.add_hline(y=4.0, line_dash="dash", line_color="#ff4444",
                  annotation_text="RED (4.0)")
    fig.update_layout(
        xaxis_title="Timestamp", yaxis_title="Anomaly Score",
        height=350, margin=dict(t=20, b=20),
        plot_bgcolor="#111111", paper_bgcolor="#111111",
        font=dict(color="#cccccc"),
        legend=dict(bgcolor="#222222"),
    )
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("No scoring data yet — waiting for the scorer to start.")

# ── Section 3: Override Audit Log ─────────────────────────────────────────────

st.divider()
st.subheader("Override Audit Log")

audit_df = db_read("""
    SELECT override_ts, device_id, sensor_type, level,
           score, override_status, override_by
    FROM   alerts
    WHERE  override_status != 'PENDING'
    ORDER  BY override_ts DESC
""")

if audit_df is None or audit_df.empty:
    st.info("No overrides recorded yet.")
else:
    st.dataframe(audit_df, use_container_width=True, hide_index=True)

# ── Auto-refresh every 5 seconds ──────────────────────────────────────────────

time.sleep(5)
st.rerun()
