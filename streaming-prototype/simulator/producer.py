"""
Sensor simulator: publishes 4 streams (2 pumps × 2 sensors) at 1 msg/sensor/sec.
Every 60–120 seconds one anomaly is injected on a randomly chosen device+sensor.
Retries Kafka connection every 5 seconds until the broker is ready.
"""

import json
import os
import random
import time
from datetime import datetime, timezone

from kafka import KafkaProducer

KAFKA_BROKER = os.getenv("KAFKA_BROKER", "localhost:9092")

PUMPS: dict[str, dict[str, dict]] = {
    "pump-042": {
        "vibration":   {"mean": 0.5, "std": 0.1},
        "temperature": {"mean": 85,  "std": 2.0},
    },
    "pump-099": {
        "vibration":   {"mean": 0.3, "std": 0.08},
        "temperature": {"mean": 82,  "std": 1.5},
    },
}

SPIKE_RANGE: dict[str, tuple] = {
    "vibration":   (3, 6),
    "temperature": (3, 5),
}


def connect_producer() -> KafkaProducer:
    while True:
        try:
            p = KafkaProducer(
                bootstrap_servers=KAFKA_BROKER,
                value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            )
            print(f"[producer] Connected to Kafka at {KAFKA_BROKER}")
            return p
        except Exception as exc:
            print(f"[producer] Kafka not ready ({exc}), retrying in 5s ...")
            time.sleep(5)


def main() -> None:
    producer        = connect_producer()
    next_anomaly_at = time.monotonic() + random.uniform(60, 120)
    inject_device: str | None = None
    inject_sensor: str | None = None

    while True:
        now = time.monotonic()

        if now >= next_anomaly_at:
            inject_device   = random.choice(list(PUMPS))
            inject_sensor   = random.choice(["vibration", "temperature"])
            next_anomaly_at = now + random.uniform(60, 120)
            print(f"[producer] ⚡ Anomaly → {inject_device}/{inject_sensor}")
        else:
            inject_device = inject_sensor = None

        ts = datetime.now(timezone.utc).isoformat()

        for device_id, sensors in PUMPS.items():
            for sensor_type, params in sensors.items():
                mean: float = params["mean"]
                std:  float = params["std"]

                if device_id == inject_device and sensor_type == inject_sensor:
                    lo, hi = SPIKE_RANGE[sensor_type]
                    value  = mean + random.uniform(lo, hi) * std
                else:
                    value  = random.gauss(mean, std)

                topic = f"topic-{sensor_type}"
                producer.send(topic, {
                    "device_id":   device_id,
                    "sensor_type": sensor_type,
                    "value":       round(value, 4),
                    "timestamp":   ts,
                })
                print(f"[SEND] {device_id}/{sensor_type}: {value:.4f} → {topic}")

        producer.flush()
        time.sleep(1)


if __name__ == "__main__":
    main()
