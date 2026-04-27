"""
Bayesian scorer: consumes topic-vibration and topic-temperature,
scores each reading against a NIG posterior predictive, logs to DuckDB,
and forwards RED/YELLOW readings to topic-alerts.

Override effects are applied retroactively by a background thread that
polls the alerts table for operator decisions from the HITL dashboard:

  IGNORE_ANOMALY      → skip prior update (don't pollute baseline)
  ACKNOWLEDGED_OUTLIER → skip prior update (real anomaly, confirmed)
  TRANSITION_STATE    → apply update normally (readings are new normal)
  REGIME_CHANGE       → reset prior to initial values (baseline changed)
"""

import json
import os
import threading
import time
from datetime import datetime, timezone

import duckdb
from kafka import KafkaConsumer, KafkaProducer

from bayesian_detector import BayesianAnomalyDetector
from init_db import init_db

KAFKA_BROKER     = os.getenv("KAFKA_BROKER", "localhost:9092")
DB_PATH          = os.getenv("DB_PATH",      "./sensor.duckdb")
YELLOW_THRESHOLD = 3.0
RED_THRESHOLD    = 4.0

# beta0 calibrated so prior predictive scale ≈ sensor's natural std dev.
# Formula: beta0 = sigma² * alpha0 * nu0 / (nu0 + 1)
INITIAL_PARAMS: dict[tuple, dict] = {
    ("pump-042", "vibration"):   dict(mu0=0.5, nu0=10, alpha0=5, beta0=0.05),
    ("pump-042", "temperature"): dict(mu0=85,  nu0=10, alpha0=5, beta0=18.0),
    ("pump-099", "vibration"):   dict(mu0=0.3, nu0=10, alpha0=5, beta0=0.03),
    ("pump-099", "temperature"): dict(mu0=82,  nu0=10, alpha0=5, beta0=10.0),
}

scorers     = {k: BayesianAnomalyDetector(**v) for k, v in INITIAL_PARAMS.items()}
scorer_lock = threading.Lock()

# Short-lived connections per operation so the file is not held open between
# writes, giving the dashboard process a window to write override decisions.
# db_lock serialises the two threads within this process.
db_lock = threading.Lock()


def _db_exec(sql: str, params: tuple = ()) -> None:
    with db_lock:
        conn = duckdb.connect(DB_PATH)
        conn.execute(sql, list(params))
        conn.commit()
        conn.close()


def _db_read(sql: str, params: tuple = ()) -> list:
    with db_lock:
        conn = duckdb.connect(DB_PATH)
        rows = conn.execute(sql, list(params)).fetchall()
        conn.close()
        return rows


# ── Kafka helpers ─────────────────────────────────────────────────────────────

def connect_kafka() -> tuple[KafkaConsumer, KafkaProducer]:
    while True:
        try:
            consumer = KafkaConsumer(
                "topic-vibration", "topic-temperature",
                bootstrap_servers=KAFKA_BROKER,
                group_id="scorer-group",
                value_deserializer=lambda v: json.loads(v.decode("utf-8")),
                auto_offset_reset="latest",
            )
            producer = KafkaProducer(
                bootstrap_servers=KAFKA_BROKER,
                value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            )
            print(f"[scorer] Connected to Kafka at {KAFKA_BROKER}")
            return consumer, producer
        except Exception as exc:
            print(f"[scorer] Kafka not ready ({exc}), retrying in 5s ...")
            time.sleep(5)


# ── Override applier (background thread) ──────────────────────────────────────

def override_applier() -> None:
    """
    Polls alerts for operator decisions and applies their effect to the
    in-memory scorer priors.  Runs every 2 seconds.
    """
    while True:
        time.sleep(2)
        try:
            rows = _db_read("""
                SELECT id, device_id, sensor_type, raw_value, override_status
                FROM   alerts
                WHERE  override_status != 'PENDING'
                AND    prior_applied   = FALSE
            """)

            for alert_id, device_id, sensor_type, raw_value, override_status in rows:
                key = (device_id, sensor_type)
                with scorer_lock:
                    if override_status in ("IGNORE_ANOMALY", "ACKNOWLEDGED_OUTLIER"):
                        pass  # prior update intentionally skipped
                    elif override_status == "TRANSITION_STATE":
                        scorers[key].update(raw_value)
                    elif override_status == "REGIME_CHANGE":
                        scorers[key].reset()
                        print(f"[REGIME_CHANGE] Prior reset → {device_id}/{sensor_type}")

                _db_exec(
                    "UPDATE alerts SET prior_applied = TRUE WHERE id = ?",
                    (alert_id,),
                )
                print(
                    f"[override] {override_status} applied "
                    f"— alert {alert_id} ({device_id}/{sensor_type})"
                )

        except Exception as exc:
            print(f"[override_applier] error: {exc}")


# ── Main consumer loop ────────────────────────────────────────────────────────

def main() -> None:
    with db_lock:
        conn = duckdb.connect(DB_PATH)
        init_db(conn)
        conn.close()

    consumer, producer = connect_kafka()
    threading.Thread(target=override_applier, daemon=True).start()

    for msg in consumer:
        data        = msg.value
        device_id   = data["device_id"]
        sensor_type = data["sensor_type"]
        value       = float(data["value"])
        ts          = data["timestamp"]
        key         = (device_id, sensor_type)

        if key not in scorers:
            continue

        with scorer_lock:
            score  = scorers[key].score(value)
            params = scorers[key].get_params()

        if score > RED_THRESHOLD:
            level = "RED"
        elif score > YELLOW_THRESHOLD:
            level = "YELLOW"
        else:
            level = "NORMAL"

        _db_exec("""
            INSERT INTO scoring_log
                (ts, device_id, sensor_type, raw_value, score, level,
                 mu, nu, alpha, beta)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (ts, device_id, sensor_type, value, score, level,
              params["mu0"], params["nu0"], params["alpha0"], params["beta0"]))

        if level == "NORMAL":
            with scorer_lock:
                scorers[key].update(value)
        else:
            # Prior update deferred — applied after operator override decision.
            _db_exec("""
                INSERT INTO alerts
                    (id, ts, device_id, sensor_type, raw_value, score, level)
                VALUES (nextval('alert_id_seq'), ?, ?, ?, ?, ?, ?)
            """, (ts, device_id, sensor_type, value, score, level))

            producer.send("topic-alerts", {**data, "score": score, "level": level})
            producer.flush()

        print(f"[{level:6s}] {device_id}/{sensor_type}: "
              f"value={value:.4f}  score={score:.4f}")


if __name__ == "__main__":
    main()
