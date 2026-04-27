import os

import duckdb

DB_PATH = os.getenv("DB_PATH", "./sensor.duckdb")


def init_db(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("CREATE SEQUENCE IF NOT EXISTS alert_id_seq START 1")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scoring_log (
            ts          TIMESTAMP,
            device_id   VARCHAR,
            sensor_type VARCHAR,
            raw_value   DOUBLE,
            score       DOUBLE,
            level       VARCHAR,
            mu          DOUBLE,
            nu          DOUBLE,
            alpha       DOUBLE,
            beta        DOUBLE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id              INTEGER PRIMARY KEY,
            ts              TIMESTAMP,
            device_id       VARCHAR,
            sensor_type     VARCHAR,
            raw_value       DOUBLE,
            score           DOUBLE,
            level           VARCHAR,
            override_status VARCHAR DEFAULT 'PENDING',
            override_by     VARCHAR,
            override_ts     TIMESTAMP,
            override_note   VARCHAR,
            prior_applied   BOOLEAN DEFAULT FALSE
        )
    """)
    conn.commit()


if __name__ == "__main__":
    conn = duckdb.connect(DB_PATH)
    init_db(conn)
    conn.close()
    print(f"Database initialised at {DB_PATH}")
