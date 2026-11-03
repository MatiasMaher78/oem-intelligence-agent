"""Separate, opt-in statistical scenario; the Delivery 01/02 seed is unchanged."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from .stage2_db import seed_stage2

EXTRA_OFFERS = tuple(
    ("DEMO-MARKET-A" if index <= 5 else "DEMO-MARKET-B", f"STATS-{index:02d}",
     "DEMO-S2-BULK-0001", "Alternador", "MarcaDemo-C", "ModeloDemo-C1", "VersionDemo-Base",
     index, index * 1000, "2026-11-03")
    for index in range(1, 11)
)


def seed_statistics_demo(path: Path) -> None:
    """Create a fresh standalone DB with ten comparable offers for inventory 10."""
    seed_stage2(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        with conn:
            conn.executemany("""
                INSERT INTO stage2_market_offers
                    (marketplace_id, offer_id, oem, part_name, brand, model, version,
                     available_units, price_cents, observed_on)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, EXTRA_OFFERS)
