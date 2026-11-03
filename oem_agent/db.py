"""Schema, deterministic synthetic fixtures, and read-only queries."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

SCHEMA = """
CREATE TABLE inventory (
    id INTEGER PRIMARY KEY,
    part_name TEXT NOT NULL,
    oem TEXT NOT NULL,
    vehicle_segment TEXT NOT NULL,
    stock_qty INTEGER NOT NULL CHECK(stock_qty >= 0),
    days_in_stock INTEGER NOT NULL CHECK(days_in_stock >= 0),
    sales_90d INTEGER NOT NULL CHECK(sales_90d >= 0),
    our_price_eur REAL NOT NULL CHECK(our_price_eur > 0),
    oem_verified INTEGER NOT NULL CHECK(oem_verified IN (0, 1))
);
CREATE TABLE market_offers (
    id INTEGER PRIMARY KEY,
    oem TEXT NOT NULL,
    seller_code TEXT NOT NULL,
    price_eur REAL NOT NULL CHECK(price_eur > 0),
    available_units INTEGER NOT NULL CHECK(available_units >= 0),
    observed_on TEXT NOT NULL
);
CREATE INDEX idx_market_oem ON market_offers(oem);
CREATE INDEX idx_stock_review ON inventory(days_in_stock, oem_verified);
"""

# All reference codes, vehicles, prices, and sellers are invented for the demo.
INVENTORY = [
    (1, "Alternador", "DEMO-ALT-1001", "Turismo-A", 2, 125, 0, 135.0, 1),
    (2, "Modulo ABS", "DEMO-ABS-2002", "Turismo-B", 1, 20, 1, 190.0, 1),
    (3, "Faro delantero", "DEMO-LMP-3003", "Turismo-C", 3, 185, 0, 220.0, 0),
    (4, "Cremallera direccion", "DEMO-STR-4004", "Turismo-D", 1, 99, 0, 310.0, 1),
    (5, "Motor de arranque", "DEMO-STA-5005", "Turismo-E", 2, 155, 2, 85.0, 1),
    (6, "Ventilador", "DEMO-TMP-6006", "Turismo-F", 0, 93, 0, 80.0, 1),
    (7, "Electroventilador", "DEMO-FAN-7007", "Turismo-G", 2, 47, 0, 145.0, 0),
    (8, "Sensor de presion", "DEMO-SEN-8008", "Turismo-H", 1, 201, 0, 70.0, 1),
]
MARKET = [
    (1, "DEMO-ALT-1001", "MOCK-A", 95.0, 1, "2026-10-26"),
    (2, "DEMO-ALT-1001", "MOCK-B", 110.0, 2, "2026-10-26"),
    (3, "DEMO-ALT-1001", "MOCK-C", 125.0, 3, "2026-10-26"),
    (4, "DEMO-ALT-10010", "MOCK-X", 9.0, 15, "2026-10-26"), # trap: partial OEM match must fail
    (5, "DEMO-LMP-3003", "MOCK-D", 180.0, 1, "2026-10-25"),
    (6, "DEMO-STR-4004", "MOCK-A", 275.0, 2, "2026-10-26"),
    (7, "DEMO-STR-4004", "MOCK-Z", 315.0, 1, "2026-10-26"),
    (8, "DEMO-STA-5005", "MOCK-K", 78.0, 2, "2026-10-26"),
    (9, "DEMO-STA-5005", "MOCK-D", 90.0, 1, "2026-10-26"),
]


def seed_demo(path: Path, *, reset: bool = False) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not reset:
            raise FileExistsError(f"Database already exists: {path}. Use --reset explicitly.")
        path.unlink()  # Explicit opt-in, applied only to the chosen demo DB path.
    # The connection context commits/rolls back; closing releases the file handle.
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executescript(SCHEMA)
        conn.executemany("INSERT INTO inventory VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", INVENTORY)
        conn.executemany("INSERT INTO market_offers VALUES (?, ?, ?, ?, ?, ?)", MARKET)


def read_only_connection(path: Path) -> sqlite3.Connection:
    """Return a read-only connection; the caller must close it (e.g. with closing)."""
    if not Path(path).is_file():
        raise FileNotFoundError(f"No demo DB: {path}. Run: python -m oem_agent seed")
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
    except BaseException:
        conn.close()
        raise
    return conn


def list_candidates(conn: sqlite3.Connection, *, days: int, limit: int) -> list[dict]:
    rows = conn.execute("""
        SELECT * FROM inventory
        WHERE stock_qty > 0 AND (days_in_stock >= ? OR oem_verified = 0)
        ORDER BY (oem_verified = 0) DESC, days_in_stock DESC, id ASC
        LIMIT ?
    """, (days, limit)).fetchall()
    return [dict(row) for row in rows]


def market_evidence(conn: sqlite3.Connection, oem: str) -> dict:
    # Exact reference equality; intentionally NOT LIKE '%oem%'.
    offers = [dict(row) for row in conn.execute("""
        SELECT seller_code, price_eur, available_units, observed_on
        FROM market_offers WHERE oem = ? ORDER BY price_eur ASC, id ASC
    """, (oem,))]
    return {
        "oem": oem,
        "offers_count": len(offers),
        "total_listed_units": sum(o["available_units"] for o in offers),
        "min_price_eur": min((o["price_eur"] for o in offers), default=None),
        "max_price_eur": max((o["price_eur"] for o in offers), default=None),
        "offers": offers,
        "market_source": "synthetic_fixture",
    }
