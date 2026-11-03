"""Delivery 01: independent synthetic fixtures and exhaustive range selection."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path


SCHEMA = """
CREATE TABLE stage2_inventory (
    id INTEGER PRIMARY KEY,
    oem TEXT NOT NULL CHECK(length(trim(oem)) > 0),
    part_name TEXT NOT NULL CHECK(length(trim(part_name)) > 0),
    brand TEXT NOT NULL CHECK(length(trim(brand)) > 0),
    model TEXT NOT NULL CHECK(length(trim(model)) > 0),
    version TEXT NOT NULL CHECK(length(trim(version)) > 0),
    stock_qty INTEGER NOT NULL CHECK(typeof(stock_qty) = 'integer' AND stock_qty >= 0),
    days_in_stock INTEGER NOT NULL CHECK(typeof(days_in_stock) = 'integer' AND days_in_stock >= 0)
);
CREATE TABLE stage2_marketplaces (
    id TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL
);
CREATE TABLE stage2_market_offers (
    marketplace_id TEXT NOT NULL REFERENCES stage2_marketplaces(id),
    offer_id TEXT NOT NULL,
    oem TEXT NOT NULL,
    part_name TEXT NOT NULL,
    brand TEXT NOT NULL,
    model TEXT NOT NULL,
    version TEXT NOT NULL,
    available_units INTEGER NOT NULL CHECK(typeof(available_units) = 'integer' AND available_units >= 0),
    price_cents INTEGER NOT NULL CHECK(typeof(price_cents) = 'integer' AND price_cents > 0),
    observed_on TEXT NOT NULL,
    PRIMARY KEY (marketplace_id, offer_id)
);
CREATE INDEX idx_stage2_inventory_age ON stage2_inventory(days_in_stock, id);
"""

# All names, OEMs, vehicles, marketplaces, units and prices are invented.
# Eight boundary/control rows plus 24 eligible rows make truncation observable.
INVENTORY = (
    (1, "DEMO-S2-ALT-0001", "Alternador", "MarcaDemo-A", "ModeloDemo-A1", "VersionDemo-Base", 2, 59),
    (2, "DEMO-S2-ALT-0002", "Alternador", "MarcaDemo-A", "ModeloDemo-A1", "VersionDemo-Base", 2, 60),
    (3, "DEMO-S2-ABS-0003", "Modulo ABS", "MarcaDemo-B", "ModeloDemo-B1", "VersionDemo-Plus", 1, 90),
    (4, "DEMO-S2-STR-0004", "Cremallera", "MarcaDemo-B", "ModeloDemo-B1", "VersionDemo-Base", 2, 91),
    (5, "DEMO-S2-LMP-0005", "Faro delantero", "MarcaDemo-A", "ModeloDemo-A1", "VersionDemo-Plus", 0, 60),
    (6, "DEMO-S2-FAN-0006", "Ventilador", "MarcaDemo-B", "ModeloDemo-B1", "VersionDemo-Base", 0, 90),
    (7, "DEMO-S2-SEN-0007", "Sensor", "MarcaDemo-A", "ModeloDemo-A1", "VersionDemo-Plus", 3, 75),
    (8, "DEMO-S2-STA-0008", "Motor de arranque", "MarcaDemo-B", "ModeloDemo-B1", "VersionDemo-Base", 0, 75),
) + tuple(
    (10 + index, f"DEMO-S2-BULK-{index + 1:04d}",
     ("Alternador", "Faro delantero", "Sensor")[index % 3],
     "MarcaDemo-C", "ModeloDemo-C1", ("VersionDemo-Base", "VersionDemo-Plus")[index % 2],
     1 + index % 4, 61 + index)
    for index in range(24)
)

MARKETPLACES = (
    ("DEMO-MARKET-A", "Marketplace sintetico A"),
    ("DEMO-MARKET-B", "Marketplace sintetico B"),
)

# Independent source fixtures. Offer IDs are local to each marketplace.
# Prices are unit EUR cents; Delivery 01 stores them without price calculations.
MARKET_A_OFFERS = (
    ("DEMO-MARKET-A", "001", "DEMO-S2-ALT-0002", "Alternador", "MarcaDemo-A", "ModeloDemo-A1", "VersionDemo-Base", 3, 12500, "2026-11-03"),
    ("DEMO-MARKET-A", "002", "DEMO-S2-ABS-0003", "Modulo ABS", "MarcaDemo-B", "ModeloDemo-B1", "VersionDemo-Plus", 1, 21000, "2026-11-03"),
    ("DEMO-MARKET-A", "003", "DEMO-S2-ALT-00020", "Alternador", "MarcaDemo-A", "ModeloDemo-A1", "VersionDemo-Base", 8, 900, "2026-11-03"),
)
MARKET_B_OFFERS = (
    ("DEMO-MARKET-B", "001", "DEMO-S2-ALT-0002", "Alternador", "MarcaDemo-A", "ModeloDemo-A1", "VersionDemo-Base", 1, 11900, "2026-11-03"),
    ("DEMO-MARKET-B", "002", "DEMO-S2-SEN-0007", "Sensor", "MarcaDemo-A", "ModeloDemo-A1", "VersionDemo-Plus", 0, 6500, "2026-11-03"),
)

CANDIDATE_SQL = """
    SELECT id, oem, part_name, brand, model, version, stock_qty, days_in_stock
    FROM stage2_inventory
    WHERE stock_qty > 0 AND days_in_stock BETWEEN ? AND ?
    ORDER BY days_in_stock ASC, id ASC
"""


def seed_stage2(path: Path) -> None:
    """Create a new, separate fixture DB; never reset or overwrite any file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation also prevents a race from opening an existing DB.
    with path.open("xb"):
        pass
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        with conn:
            conn.executescript(SCHEMA)
            conn.executemany("INSERT INTO stage2_inventory VALUES (?, ?, ?, ?, ?, ?, ?, ?)", INVENTORY)
            conn.executemany("INSERT INTO stage2_marketplaces VALUES (?, ?)", MARKETPLACES)
            for offers in (MARKET_A_OFFERS, MARKET_B_OFFERS):
                conn.executemany("INSERT INTO stage2_market_offers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", offers)


def validate_day_range(min_days: int, max_days: int) -> None:
    if type(min_days) is not int or type(max_days) is not int or not 0 <= min_days <= max_days:
        raise ValueError("Day bounds must be non-negative integers with min_days <= max_days")


def list_stage2_candidates(conn: sqlite3.Connection, *, min_days: int = 60,
                           max_days: int = 90) -> list[dict]:
    """Return every in-stock row in the inclusive interval, in stable order."""
    validate_day_range(min_days, max_days)
    return [dict(row) for row in conn.execute(CANDIDATE_SQL, (min_days, max_days)).fetchall()]


OFFER_SQL = """
    SELECT marketplace_id, offer_id, oem, part_name, brand, model, version,
           available_units, price_cents, observed_on
    FROM stage2_market_offers
    WHERE marketplace_id = ?
      AND (oem COLLATE BINARY = ? OR part_name COLLATE BINARY = ?)
    ORDER BY offer_id ASC
"""


def list_stage2_offers(conn: sqlite3.Connection, *, marketplace_id: str,
                       oem: str | None, part_name: str | None) -> list[dict]:
    """Retrieve candidates by exact OEM OR exact piece name, within one source."""
    if marketplace_id not in {identifier for identifier, _ in MARKETPLACES}:
        raise ValueError(f"Unknown synthetic marketplace: {marketplace_id}")
    parameters = (marketplace_id, oem, part_name)
    return [dict(row) for row in conn.execute(OFFER_SQL, parameters).fetchall()]
