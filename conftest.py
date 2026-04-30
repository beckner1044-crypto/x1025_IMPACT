"""
conftest.py
Shared pytest fixtures.

We isolate every test in its own tempdir so feedback DBs, audit logs, and
ChromaDB collections never bleed between tests.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def tmp_db(tmp_path: Path) -> str:
    """A fresh SQLite mock of the x1025 system of record."""
    db_path = tmp_path / "x1025.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE vessels (
            id INTEGER PRIMARY KEY, name TEXT, imo TEXT, vessel_type TEXT,
            dwt INTEGER, cp_speed_kn REAL, cp_consumption_mt REAL
        );
        CREATE TABLE daily_reports (
            id INTEGER PRIMARY KEY, vessel_id INTEGER, report_date DATE,
            report_type TEXT, position_lat REAL, position_lon REAL,
            distance_24h_nm REAL, avg_speed_kn REAL, fuel_consumption_24h_mt REAL,
            fuel_rob_hfo_mt REAL, fuel_rob_mgo_mt REAL,
            destination_port TEXT, eta TEXT
        );
        CREATE TABLE certificates (
            id INTEGER PRIMARY KEY, vessel_id INTEGER, cert_type TEXT,
            issue_date DATE, expiry_date DATE
        );

        INSERT INTO vessels VALUES
            (1, 'MV Aurora',  '9456789', 'Aframax',   115000, 13.0, 32.0),
            (2, 'MV Boreas',  '9512345', 'Suezmax',   158000, 12.5, 44.0),
            (3, 'MV Cassini', '9678123', 'VLCC',      300000, 12.0, 65.0);

        INSERT INTO daily_reports VALUES
            (1, 2, '2026-04-27', 'noon', 1.5, 103.8, 281.3, 11.72, 46.5, 1240.5, 220.0, 'Singapore', '2026-05-10'),
            (2, 1, '2026-04-27', 'noon', 51.5, 4.5, 312.0, 13.0, 32.0, 1500.0, 240.0, 'Rotterdam', '2026-05-05');

        INSERT INTO certificates VALUES
            (1, 3, 'Safety Radio',     '2021-04-24', '2026-04-24'),  -- expired
            (2, 2, 'Safety Equipment', '2021-05-10', '2026-05-10'),  -- imminent
            (3, 1, 'IOPP',             '2024-06-15', '2029-06-15');  -- fine
        """
    )
    conn.commit()
    conn.close()
    return str(db_path)


@pytest.fixture
def tmp_logs_dir(tmp_path: Path, monkeypatch) -> Path:
    """Redirect logs/ writes into the test's tempdir."""
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    return logs
