"""
setup_data.py
Build the mock x1025 system of record in SQLite.

Run this once before launching the chatbot. It creates:
  - data/x1025.db          SQLite DB with vessels, daily_reports, certificates
  - confirms data/ism_docs/  exists with markdown ISM procedures

This stands in for the real x1025 cloud database. The schema is intentionally
small but covers everything the proposal lists for Layer 2:
ETAs, fuel ROB, charter party performance, certificate expiry.
"""
import os
import sqlite3
from datetime import date, timedelta
import random

random.seed(42)

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
DB_PATH = os.path.join(DATA_DIR, "x1025.db")
ISM_DIR = os.path.join(DATA_DIR, "ism_docs")


def build_schema(conn):
    cur = conn.cursor()
    cur.executescript(
        """
        DROP TABLE IF EXISTS daily_reports;
        DROP TABLE IF EXISTS certificates;
        DROP TABLE IF EXISTS vessels;

        CREATE TABLE vessels (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            imo TEXT NOT NULL UNIQUE,
            vessel_type TEXT,
            dwt INTEGER,
            cp_speed_kn REAL,            -- charter party warranted speed in knots
            cp_consumption_mt REAL       -- charter party consumption in MT/day at CP speed
        );

        CREATE TABLE daily_reports (
            id INTEGER PRIMARY KEY,
            vessel_id INTEGER NOT NULL,
            report_date DATE NOT NULL,
            report_type TEXT NOT NULL,   -- 'noon' | 'arrival' | 'departure'
            position_lat REAL,
            position_lon REAL,
            distance_24h_nm REAL,
            avg_speed_kn REAL,
            fuel_consumption_24h_mt REAL,
            fuel_rob_hfo_mt REAL,
            fuel_rob_mgo_mt REAL,
            destination_port TEXT,
            eta TEXT,
            FOREIGN KEY (vessel_id) REFERENCES vessels(id)
        );

        CREATE TABLE certificates (
            id INTEGER PRIMARY KEY,
            vessel_id INTEGER NOT NULL,
            cert_type TEXT NOT NULL,
            issue_date DATE NOT NULL,
            expiry_date DATE NOT NULL,
            FOREIGN KEY (vessel_id) REFERENCES vessels(id)
        );

        CREATE INDEX idx_reports_vessel_date
          ON daily_reports(vessel_id, report_date);
        CREATE INDEX idx_certs_vessel_expiry
          ON certificates(vessel_id, expiry_date);
        """
    )
    conn.commit()


def seed_vessels(conn):
    vessels = [
        # name,         imo,          type,          dwt,    cp_speed, cp_cons
        ("MV Aurora",   "9456789",   "Aframax",     115000, 13.0,    32.0),
        ("MV Boreas",   "9512345",   "Suezmax",     158000, 12.5,    44.0),
        ("MV Cassini",  "9678123",   "VLCC",        300000, 12.0,    65.0),
        ("MV Dorado",   "9701234",   "MR Tanker",    50000, 14.0,    24.0),
        ("MV Equinox",  "9789456",   "LR2 Tanker",  110000, 13.5,    34.0),
    ]
    cur = conn.cursor()
    cur.executemany(
        "INSERT INTO vessels (name, imo, vessel_type, dwt, cp_speed_kn, cp_consumption_mt) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        vessels,
    )
    conn.commit()


def seed_daily_reports(conn):
    """30 days of noon reports per vessel, with realistic variance vs CP terms."""
    cur = conn.cursor()
    cur.execute("SELECT id, name, cp_speed_kn, cp_consumption_mt FROM vessels")
    rows = cur.fetchall()
    today = date.today()

    destinations = {
        "MV Aurora":  ("Rotterdam", 7),
        "MV Boreas":  ("Singapore", 12),
        "MV Cassini": ("Fujairah",  4),
        "MV Dorado":  ("Houston",   9),
        "MV Equinox": ("Yokohama", 15),
    }

    for vid, vname, cp_speed, cp_cons in rows:
        dest, eta_days_out = destinations[vname]
        # MV Boreas under-performs the CP, MV Equinox over-performs — gives Layer 2
        # something interesting to surface.
        speed_bias = {"MV Boreas": -0.8, "MV Equinox": +0.4}.get(vname, 0.0)
        cons_bias  = {"MV Boreas": +2.5, "MV Equinox": -1.0}.get(vname, 0.0)

        rob_hfo = 1800.0
        rob_mgo = 240.0
        for d in range(30, 0, -1):
            report_date = today - timedelta(days=d)
            speed = cp_speed + speed_bias + random.uniform(-0.4, 0.4)
            cons  = cp_cons  + cons_bias  + random.uniform(-1.5, 1.5)
            distance = speed * 24
            rob_hfo = max(0.0, rob_hfo - cons)
            rob_mgo = max(0.0, rob_mgo - random.uniform(0.4, 0.7))
            lat = round(random.uniform(-5, 55), 2)
            lon = round(random.uniform(-30, 120), 2)
            eta = (today + timedelta(days=eta_days_out - d // 4)).isoformat()
            cur.execute(
                """INSERT INTO daily_reports
                   (vessel_id, report_date, report_type, position_lat, position_lon,
                    distance_24h_nm, avg_speed_kn, fuel_consumption_24h_mt,
                    fuel_rob_hfo_mt, fuel_rob_mgo_mt, destination_port, eta)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (vid, report_date.isoformat(), "noon", lat, lon,
                 round(distance, 1), round(speed, 2), round(cons, 2),
                 round(rob_hfo, 1), round(rob_mgo, 1), dest, eta),
            )
    conn.commit()


def seed_certificates(conn):
    """Certs per vessel, with one or two near-expiry to make Layer 2 alerts fire."""
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM vessels")
    today = date.today()

    cert_types = [
        "Safety Construction",
        "Safety Equipment",
        "Safety Radio",
        "IOPP",
        "IAPP",
        "Safety Management Certificate",
        "ISSC",
        "Load Line",
    ]

    for vid, name in cur.fetchall():
        for i, ct in enumerate(cert_types):
            # Stagger expiries; force one or two near-expiry per vessel
            if i == 0:
                expiry = today + timedelta(days=25)        # imminent
            elif i == 1 and name == "MV Boreas":
                expiry = today + timedelta(days=12)        # urgent
            elif i == 2 and name == "MV Cassini":
                expiry = today - timedelta(days=4)         # already expired
            else:
                expiry = today + timedelta(days=180 + i * 60)
            issue = expiry - timedelta(days=5 * 365)
            cur.execute(
                """INSERT INTO certificates
                   (vessel_id, cert_type, issue_date, expiry_date)
                   VALUES (?,?,?,?)""",
                (vid, ct, issue.isoformat(), expiry.isoformat()),
            )
    conn.commit()


def verify_ism_docs():
    if not os.path.isdir(ISM_DIR):
        raise SystemExit(f"Expected ISM docs at {ISM_DIR} — directory missing.")
    docs = [f for f in os.listdir(ISM_DIR) if f.endswith(".md")]
    if not docs:
        raise SystemExit(f"No .md ISM documents found in {ISM_DIR}.")
    print(f"Found {len(docs)} ISM documents in {ISM_DIR}")


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    verify_ism_docs()

    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    build_schema(conn)
    seed_vessels(conn)
    seed_daily_reports(conn)
    seed_certificates(conn)

    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM vessels")
    n_vessels = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM daily_reports")
    n_reports = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM certificates")
    n_certs = cur.fetchone()[0]
    conn.close()

    print(f"Built {DB_PATH}")
    print(f"  vessels:        {n_vessels}")
    print(f"  daily_reports:  {n_reports}")
    print(f"  certificates:   {n_certs}")


if __name__ == "__main__":
    main()
