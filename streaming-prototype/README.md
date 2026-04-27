# Bayesian Streaming Prototype — APS Predictive Maintenance Simulation

## What This Project Does

A real-time anomaly detection pipeline simulating IoT sensor monitoring for power plant equipment. Two simulated pumps stream vibration and temperature data through Kafka. A Bayesian Normal-Inverse-Gamma (NIG) scorer evaluates each reading against a posterior predictive distribution. Anomalies are routed to a separate Kafka topic and displayed in a Streamlit HITL (Human-in-the-Loop) dashboard where operators can review and override alerts using a four-state taxonomy.

This prototype demonstrates the same Bayesian WAP governance architecture described in `../paper/bayesian_wap_architecture.pdf`, adapted from batch CI/CD to real-time streaming.

## Architecture

```
sensor-simulator (producer.py)
  ├── pump-042: vibration + temperature → every 1s
  └── pump-099: vibration + temperature → every 1s
         │                    │
         ▼                    ▼
   topic-vibration      topic-temperature
         │                    │
         └────────┬───────────┘
                  ▼
    bayesian-scorer (consumer_scorer.py)
      ├── 4 independent NIG scorers (2 devices × 2 sensors)
      ├── Each reading: score against posterior → update prior
      ├── All readings logged to DuckDB (scoring_log table)
      ├── score > 3.0 (YELLOW) or > 4.0 (RED) → write to topic-alerts
      └── Normal → log only, no alert
                  │
                  ▼
           topic-alerts
                  │
                  ▼
    streamlit-hitl (app.py)
      ├── Reads topic-alerts on startup, then polls
      ├── Displays pending alerts (RED first, then YELLOW)
      ├── 4-state HITL override buttons per alert:
      │     IGNORE_ANOMALY, REGIME_CHANGE,
      │     ACKNOWLEDGED_OUTLIER, TRANSITION_STATE
      ├── Device health score trend chart (Plotly)
      └── Override audit log table
                  │
            DuckDB (sensor.duckdb)
              ├── scoring_log table (all readings)
              └── alerts table (anomalies + override status)
```

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Message broker | Apache Kafka (via Confluent Docker image) |
| Broker coordinator | Zookeeper (via Confluent Docker image) |
| Sensor simulation | Python + kafka-python |
| Bayesian scoring | Python (custom NIG class, no external ML library) |
| Database | DuckDB (embedded, file-based) |
| HITL Dashboard | Streamlit + Plotly |
| Containerization | Docker + docker-compose |

## File Structure

```
streaming-prototype/
├── docker-compose.yml          # Kafka + Zookeeper + 3 app containers
├── requirements.txt            # kafka-python, duckdb, streamlit, plotly
├── README.md                   # This file
│
├── simulator/
│   ├── Dockerfile
│   └── producer.py             # Simulates 2 pumps, 2 sensor types each
│
├── scorer/
│   ├── Dockerfile
│   ├── bayesian_detector.py    # BayesianAnomalyDetector class (NIG)
│   ├── consumer_scorer.py      # Kafka consumer → score → DuckDB + alert topic
│   └── init_db.py              # Create DuckDB tables
│
├── dashboard/
│   ├── Dockerfile
│   ├── app.py                  # Streamlit HITL dashboard
│   └── requirements.txt        # streamlit, duckdb, plotly, pandas
```

## File Specifications

### 1. `scorer/bayesian_detector.py`

```python
class BayesianAnomalyDetector:
    """
    Normal-Inverse-Gamma conjugate model for sequential anomaly detection.
    
    Prior parameters:
        mu0: prior mean
        nu0: prior sample size (pseudo-observations)
        alpha0: shape parameter (controls variance uncertainty)
        beta0: scale parameter
    
    Methods:
        score(x) -> float: Returns absolute Student-t statistic.
                           Higher = more anomalous.
        update(x) -> None: Bayesian update. Posterior becomes new prior.
        get_params() -> dict: Returns current (mu0, nu0, alpha0, beta0).
    
    Scoring thresholds:
        score > 3.0 → YELLOW (suspicious)
        score > 4.0 → RED (anomaly)
    
    Key property: O(1) per observation. No windowing needed.
    The prior accumulates history — it IS the baseline.
    """
```

### 2. `simulator/producer.py`

```
Simulates 2 pumps (pump-042, pump-099), each with 2 sensors (vibration, temperature).

Normal behavior:
  pump-042 vibration: mean=0.5, std=0.1 (units: g)
  pump-042 temperature: mean=85, std=2 (units: °F)
  pump-099 vibration: mean=0.3, std=0.08
  pump-099 temperature: mean=82, std=1.5

Anomaly injection:
  Every 60-120 seconds (random), inject ONE anomaly:
  - Vibration spike: value = normal_mean + random(3, 6) * normal_std
  - Temperature spike: value = normal_mean + random(3, 5) * normal_std
  Only one device/sensor at a time, to simulate realistic single-point failures.

Message format (JSON):
{
    "device_id": "pump-042",
    "sensor_type": "vibration",
    "value": 2.8,
    "timestamp": "2026-04-26T10:00:01.123Z"
}

Topics:
  - vibration readings → "topic-vibration"
  - temperature readings → "topic-temperature"

Send rate: 1 message per sensor per second = 4 messages/sec total.

Retry logic: If Kafka broker is not ready, retry connection every 5 seconds
(Kafka container may take 15-30 seconds to start).
```

### 3. `scorer/consumer_scorer.py`

```
Subscribes to: "topic-vibration", "topic-temperature"
Consumer group: "scorer-group"

On startup:
  1. Initialize DuckDB at DB_PATH (env var, default: ./sensor.duckdb)
  2. Create tables if not exist (call init_db.py)
  3. Create 4 BayesianAnomalyDetector instances:
     - ("pump-042", "vibration"):   mu0=0.5, nu0=10, alpha0=5, beta0=0.05
     - ("pump-042", "temperature"): mu0=85,  nu0=10, alpha0=5, beta0=18.0
     - ("pump-099", "vibration"):   mu0=0.3, nu0=10, alpha0=5, beta0=0.03
     - ("pump-099", "temperature"): mu0=82,  nu0=10, alpha0=5, beta0=10.0
     Note: beta0 calibrated so prior predictive scale ≈ sensor's natural std dev.
     Formula: beta0 = sigma² × alpha0 × nu0 / (nu0 + 1)

For each message:
  1. Parse JSON
  2. Look up scorer by (device_id, sensor_type)
  3. score = scorer.score(value)
  4. Determine level: "NORMAL" / "YELLOW" (>3.0) / "RED" (>4.0)
  5. INSERT into scoring_log (ts, device_id, sensor_type, value, score, level, mu, nu, alpha, beta)
  6. If YELLOW or RED:
     - Send to "topic-alerts" with same JSON + score + level fields
     - INSERT into alerts table with override_status = 'PENDING'
  7. Check DuckDB for active override on this (device_id, sensor_type):
     - IGNORE_ANOMALY or ACKNOWLEDGED_OUTLIER → do NOT call scorer.update(value)
     - REGIME_CHANGE → call scorer.reset(initial_params), then clear the override
     - TRANSITION_STATE or no override → call scorer.update(value) normally
  8. Print to console: "[NORMAL/YELLOW/RED] pump-042/vibration: value=2.8, score=1.2"

Retry logic: Same as producer — retry Kafka connection every 5 seconds.
```

### 4. `scorer/init_db.py`

```sql
CREATE TABLE IF NOT EXISTS scoring_log (
    ts TIMESTAMP,
    device_id VARCHAR,
    sensor_type VARCHAR,
    raw_value DOUBLE,
    score DOUBLE,
    level VARCHAR,
    mu DOUBLE,
    nu DOUBLE,
    alpha DOUBLE,
    beta DOUBLE
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY,
    ts TIMESTAMP,
    device_id VARCHAR,
    sensor_type VARCHAR,
    raw_value DOUBLE,
    score DOUBLE,
    level VARCHAR,
    override_status VARCHAR DEFAULT 'PENDING',
    override_by     VARCHAR,
    override_ts     TIMESTAMP,
    override_note   VARCHAR,
    prior_applied   BOOLEAN DEFAULT FALSE
);
```

### 5. `dashboard/app.py`

```
Streamlit app on port 8501.

Layout:
  1. Title: "Bayesian Sensor Monitoring — HITL Dashboard"
  
  2. Section: "Pending Alerts"
     - Query alerts table WHERE override_status = 'PENDING'
     - Sort by level DESC (RED first), then score DESC
     - Display as dataframe
     - For each alert row, show 4 buttons:
       IGNORE_ANOMALY | REGIME_CHANGE | ACKNOWLEDGED_OUTLIER | TRANSITION_STATE
     - On button click: UPDATE alerts SET override_status=X,
       override_by='operator', override_ts=now(), override_note=''
     - st.rerun() after override
  
  3. Section: "Device Health Trends"
     - Dropdown: select device (pump-042 / pump-099)
     - Query scoring_log for selected device, last 200 readings
     - Plotly line chart: x=timestamp, y=score, color=sensor_type
     - Horizontal lines at y=3.0 (YELLOW threshold) and y=4.0 (RED threshold)
  
  4. Section: "Override Audit Log"
     - Query alerts WHERE override_status != 'PENDING'
     - Sort by override_ts DESC
     - Display as dataframe

DuckDB path: DB_PATH env var, default: ./sensor.duckdb
Auto-refresh: st.rerun() every 5 seconds (use st.empty() + time.sleep pattern
or streamlit-autorefresh)
```

### 6. `docker-compose.yml`

```
5 services:

1. zookeeper:
   - image: confluentinc/cp-zookeeper:7.5.0
   - port: 2181
   
2. kafka:
   - image: confluentinc/cp-kafka:7.5.0
   - port: 9092
   - depends_on: zookeeper
   - KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://kafka:9092
     (use "kafka" not "localhost" because containers talk to each other)
   
3. sensor-simulator:
   - build: ./simulator
   - depends_on: kafka
   - env: KAFKA_BROKER=kafka:9092
   
4. bayesian-scorer:
   - build: ./scorer
   - depends_on: kafka
   - env: KAFKA_BROKER=kafka:9092, DB_PATH=/data/sensor.duckdb
   - volume: db-data:/data
   
5. streamlit-hitl:
   - build: ./dashboard
   - depends_on: kafka
   - port: 8501:8501
   - env: KAFKA_BROKER=kafka:9092, DB_PATH=/data/sensor.duckdb
   - volume: db-data:/data (same volume as scorer, shared DuckDB)

shared volume:
  db-data: (named volume, shared between scorer and dashboard)
```

### 7. Dockerfiles (all 3 are similar)

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "producer.py"]   # or consumer_scorer.py, or streamlit run app.py
```

### 8. `requirements.txt` (root)

```
kafka-python
duckdb
streamlit
plotly
```

## Build & Run Steps

### Option A: Local development (no Docker, fastest for testing)

```bash
# Terminal 1: Start Kafka + Zookeeper only
docker-compose up zookeeper kafka

# Terminal 2: Set up Python environment
cd streaming-prototype
uv venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Mac/Linux
uv pip install kafka-python duckdb streamlit plotly

# Terminal 2: Run producer
python simulator/producer.py

# Terminal 3: Run scorer
python scorer/consumer_scorer.py

# Terminal 4: Run dashboard
streamlit run dashboard/app.py
```

### Option B: Full Docker (production-like)

```bash
cd streaming-prototype
docker-compose up --build
# Open http://localhost:8501 for Streamlit dashboard
```

## HITL Override Taxonomy — Streaming Behavior

Each override state has a specific effect on the Bayesian prior:

### IGNORE_ANOMALY → Skip prior update

The reading has a known explanation (e.g., planned pressure test). The value is NOT a real anomaly.

- **Prior behavior:** Do NOT call `scorer.update(x)`. The anomalous value should not shift the posterior.
- **Example:** pump-042 vibration spikes to 1.8g during a scheduled pressure test. Operator marks IGNORE_ANOMALY. The prior stays at mu ≈ 0.5g. After the test ends, the scorer resumes normal monitoring with an uncontaminated baseline.

### ACKNOWLEDGED_OUTLIER → Skip prior update

The reading IS a real anomaly, confirmed by the operator. Crew has been dispatched.

- **Prior behavior:** Same as IGNORE_ANOMALY — do NOT call `scorer.update(x)`. The difference is semantic: the anomaly is real, but it should not shift the baseline.
- **Example:** pump-042 vibration spikes to 3.2g from bearing damage. Operator confirms and dispatches crew. The prior stays at mu ≈ 0.5g so that after repair, the scorer still knows what "normal" looks like.

### REGIME_CHANGE → Reset prior to initial values

The equipment's baseline has permanently changed (e.g., new pump model installed, major overhaul).

- **Prior behavior:** Reset `(mu0, nu0, alpha0, beta0)` back to initial values for that (device_id, sensor_type). The old posterior is invalid.
- **Example:** Old pump-042 (normal vibration ~0.5g) is replaced with a new model (normal vibration ~0.8g). Without reset, the scorer would flag every reading from the new pump. After REGIME_CHANGE, the scorer re-learns the new baseline from fresh data within ~50 observations.
- **Log the reset event** with timestamp in the audit trail.

### TRANSITION_STATE → Keep updating normally

The equipment is undergoing a planned temporary fluctuation (startup, shutdown, maintenance cycle). The readings are physically valid but temporarily unusual.

- **Prior behavior:** Continue calling `scorer.update(x)` normally. The transition values should be absorbed into the posterior.
- **Example:** pump-042 undergoes planned shutdown (vibration drops 0.5 → 0) then restart (vibration rises 0 → 0.5). These values are temporary but real. The prior will naturally re-stabilize once the equipment reaches steady state.
- **Why keep updating:** Transition data contains useful diagnostic information. Startup to 0.6g then stable at 0.5g = normal. Startup to 1.2g then stable at 0.9g = possible installation issue. Skipping updates would lose this signal.
- **Why not reset:** The equipment baseline hasn't permanently changed. The same pump will return to ~0.5g. Resetting would discard months of accumulated prior knowledge.

### Summary

| State | Prior Action | Scenario |
|-------|-------------|----------|
| IGNORE_ANOMALY | Skip update | Known non-issue (pressure test, calibration) |
| ACKNOWLEDGED_OUTLIER | Skip update | Confirmed real anomaly (crew dispatched) |
| REGIME_CHANGE | Reset to initial values | Baseline permanently changed (new equipment) |
| TRANSITION_STATE | Keep updating | Planned temporary fluctuation (startup/shutdown) |

### Implementation note

Override decisions are applied by a **background thread** (`override_applier`) that polls the `alerts` table every 2 seconds for rows where `override_status != 'PENDING'` and `prior_applied = FALSE`. The main Kafka consumer loop does not block waiting for operator input.

The prior update is **deferred** for anomalous readings and applied retroactively once the operator decides:

```python
# Pseudocode in consumer_scorer.py — main consumer loop
score = scorer.score(value)       # Always score
log_to_scoring_log(...)           # Always log

if level == "NORMAL":
    scorer.update(value)          # Immediate update — no override needed
else:  # YELLOW or RED
    insert_alert(override_status='PENDING', prior_applied=False)
    send_to_topic_alerts(...)
    # Prior update deferred — override_applier thread handles it

# --- override_applier thread (runs every 2 s) ---
for alert in alerts where override_status != 'PENDING' and prior_applied = False:
    if alert.override in ("IGNORE_ANOMALY", "ACKNOWLEDGED_OUTLIER"):
        pass                      # Skip update — don't pollute prior
    elif alert.override == "TRANSITION_STATE":
        scorer.update(alert.raw_value)   # Apply deferred update
    elif alert.override == "REGIME_CHANGE":
        scorer.reset()            # Restore (mu0, nu0, alpha0, beta0) to initial values
    mark prior_applied = True
```

## Key Design Decisions

1. **DuckDB over SQL Server**: Embedded, zero-config, file-based. No external database container needed. In production, swap for enterprise database.

2. **Separate topics for vibration and temperature**: Different sensor types have different normal distributions. Separating topics makes it explicit. Consumer subscribes to both.

3. **4 independent scorers**: Each (device, sensor_type) pair has its own NIG prior. Vibration normal ~0.5g is completely different from temperature normal ~85°F. Cannot share priors.

4. **HITL override scoped per device + per sensor**: IGNORE_ANOMALY on pump-042/vibration does NOT suppress pump-042/temperature monitoring. Cross-sensor correlation is logged but not auto-suppressed.

5. **Shared DuckDB volume**: Scorer writes, dashboard reads. Same file, same volume mount. DuckDB handles concurrent read safely.

6. **No Spark**: Bayesian NIG scoring is O(1) per observation. No windowing or aggregation needed. kafka-python consumer is sufficient for this throughput.

## Connection to Production System

| This Prototype | ADE Production |
|----------------|----------------|
| Kafka topics | Azure DevOps pipeline artifacts |
| DuckDB | SQL Server |
| Streamlit HITL dashboard | Streamlit + ADO re-triggering |
| topic-alerts | Pipeline circuit breaker RED/YELLOW |
| 4-state override buttons | Same 4-state HITL taxonomy |
| docker-compose | Azure DevOps YAML pipeline |
| sensor simulator | Real data extraction stage |

## Author

Kaihua (Kai) Chang — [arXiv:2510.22419v2](https://arxiv.org/abs/2510.22419v2)