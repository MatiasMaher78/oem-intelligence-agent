"""Five independent, invented evaluation snapshots; never extend the 27-piece seed."""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
from unittest.mock import patch

from .stage2_db import SCHEMA, MARKETPLACES
from .stage2_matching import list_stage2_offers
from .stage3_statistics import execute_stage2_statistics
from .stage4_review import create_dossier, fingerprint
from .stage5_evidence import EvidenceSnapshot

DATASET_VERSION = "oem-eval-5.5-v1"
# Authored constants are the oracle, not recomputed expected values from the model.
GROUND_TRUTH = {
    "E1": {"oem": "0000421001", "evidence_status": "sufficient_sample_demo",
           "observed_offers_count": 10, "observed_units": 55,
           "observed_min_price_cents": 1000, "observed_max_price_cents": 10000,
           "filtered_min_price_cents": 2000, "filtered_max_price_cents": 9000,
           "assessment": "possible_price_anomaly_demo", "abstain": False},
    "E2": {"oem": "EV5A0102B", "evidence_status": "no_comparable_offers",
           "observed_offers_count": 0, "observed_units": 0,
           "observed_min_price_cents": None, "observed_max_price_cents": None,
           "filtered_min_price_cents": None, "filtered_max_price_cents": None,
           "exact_incompatible": 1, "exact_unknown": 1, "unknown_confirmed": 1, "different_confirmed": 1,
           "catalog_status": "not_assessed", "assessment": "incompatible_offers", "abstain": True},
    "E3": {"oem": "EV5-AB/03.01", "evidence_status": "sufficient_sample_demo",
           "observed_offers_count": 10, "observed_units": 20,
           "observed_min_price_cents": 5000, "observed_max_price_cents": 14000,
           "filtered_min_price_cents": 6000, "filtered_max_price_cents": 13000,
           "assessment": "competitive_price_demo", "abstain": False},
    "E4": {"oem": "9990000004", "evidence_status": "no_comparable_offers",
           "observed_offers_count": 0, "observed_units": 0,
           "observed_min_price_cents": None, "observed_max_price_cents": None,
           "filtered_min_price_cents": None, "filtered_max_price_cents": None,
           "catalog_status": "not_assessed", "assessment": "no_offers", "abstain": True},
    "E5": {"oem": "EV5-X/05", "evidence_status": "source_error",
           "observed_offers_count": 5, "observed_units": 15,
           "observed_min_price_cents": 7000, "observed_max_price_cents": 11000,
           "filtered_min_price_cents": None, "filtered_max_price_cents": None,
           "catalog_status": "not_assessed", "assessment": "source_error", "abstain": True},
}
SCENARIOS = {
    "E1": (1001, "AlternadorEval", 60, 1, "Compara un precio hipotetico de 19000 centimos con la banda filtrada DEMO. Es solo una posible anomalia?"),
    "E2": (1002, "SensorEval", 65, 2, "Distingue OEM exacto, distinto y desconocido y compatibilidad incompatible, desconocida y confirmada. Hay ofertas utilizables? No diagnostiques la catalogacion."),
    "E3": (1003, "FaroEval", 75, 3, "Un precio hipotetico de 6500 centimos esta dentro de la banda filtrada DEMO? Describe competitividad solo en ese sentido, sin recomendar cambios."),
    "E4": (1004, "VentiladorEval", 85, 4, "Hay ofertas? Puedes validar un precio o concluir que la catalogacion es incorrecta?"),
    "E5": (1005, "ArranqueEval", 90, 5, "Hay cobertura completa de los dos marketplaces? Conserva las ofertas conocidas, explica el error y abstente de validar precios."),
}


def question(case_id):
    identifier, _, _, _, text = SCENARIOS[case_id]
    keys = ", ".join(GROUND_TRUTH[case_id])
    return (f"Evidencia sintetica independiente. Pieza {identifier}, OEM literal {GROUND_TRUTH[case_id]['oem']}. {text} "
            f"Responde un unico objeto JSON con facts (claves exactas: {keys}) y explanation breve en espanol. "
            "Precios en centimos enteros; OEM texto; valores ausentes null; abstain booleano significa abstenerse de validar precios. "
            "assessment debe ser una de: possible_price_anomaly_demo, competitive_price_demo, incompatible_offers, no_offers, source_error. "
            "Consulta evidencia con herramientas nativas. No acciones comerciales ni aprobaciones.")


def _offers(case_id, item):
    def offer(index, price, units, **changes):
        row = dict(zip(("oem", "part_name", "brand", "model", "version"), item[1:6]))
        row.update(marketplace_id="DEMO-MARKET-A" if index % 2 else "DEMO-MARKET-B",
                   offer_id=f"{case_id}-{index:02d}", available_units=units, price_cents=price, observed_on="2026-11-04")
        row.update(changes)
        return row
    if case_id == "E1":
        return [offer(i, i*1000, i) for i in range(1, 11)] + [offer(11, 500, 99, oem="00004210010")]
    if case_id == "E2":
        return [offer(1, 1000, 1, version="OtraVersion"), offer(2, 2000, 1, version=""),
                offer(3, 3000, 1, oem=""), offer(4, 4000, 0, oem="ev5a0102b")]
    if case_id == "E3":
        return [offer(i, (i+4)*1000, 2) for i in range(1, 11)] + [offer(11, 100, 99, oem="EV5AB0301")]
    if case_id == "E5":
        return [offer(i, (i+6)*1000, 3, marketplace_id="DEMO-MARKET-B") for i in range(1, 6)]
    return []


def build_dataset():
    """Actual existing SQL/matching/math; one explicit source fault, no persistent DBs."""
    snapshots = {}
    for case_id, (identifier, name, age, stock, _) in SCENARIOS.items():
        item = (identifier, GROUND_TRUTH[case_id]["oem"], name, "MarcaEval", "ModeloEval", "BaseEval", stock, age)
        offers = _offers(case_id, item)
        with tempfile.TemporaryDirectory(prefix="oem-eval-") as directory:
            path = Path(directory)/"independent.sqlite"
            with closing(sqlite3.connect(path)) as conn:
                conn.executescript(SCHEMA)
                with conn:
                    conn.executemany("INSERT INTO stage2_inventory VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [item])
                    conn.executemany("INSERT INTO stage2_marketplaces VALUES (?, ?)", MARKETPLACES)
                    conn.executemany("INSERT INTO stage2_market_offers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        [(o['marketplace_id'], o['offer_id'], o['oem'], o['part_name'], o['brand'], o['model'],
                          o['version'], o['available_units'], o['price_cents'], o['observed_on']) for o in offers])
            def read(conn, *, marketplace_id, oem, part_name):
                if case_id == "E5" and marketplace_id == "DEMO-MARKET-A":
                    raise sqlite3.OperationalError("DEMO evaluation source unavailable")
                return list_stage2_offers(conn, marketplace_id=marketplace_id, oem=oem, part_name=part_name)
            # Fixture-only injection at the existing SQL boundary; real error tracing is preserved.
            with patch("oem_agent.stage2_matching.list_stage2_offers", side_effect=read):
                evidence = execute_stage2_statistics(path)
        # Canonical source label replaces the ephemeral path, not any source result or trace.
        evidence["source"]["database"] = f"synthetic:{DATASET_VERSION}/{case_id}"
        dossier = create_dossier(evidence, dossier_id=f"{DATASET_VERSION}-{case_id}", at="2026-11-04T12:00:00+00:00")
        snapshots[case_id] = EvidenceSnapshot(evidence, dossier)
    return snapshots


def dataset_identity(snapshots):
    identity = {"version": DATASET_VERSION, "ground_truth_sha256": fingerprint(GROUND_TRUTH),
                "scenarios": {key: {"snapshot_id": snap.snapshot_id, "evidence_sha256": snap.evidence_sha256,
                                      "question_sha256": fingerprint(question(key))} for key, snap in snapshots.items()}}
    return {**identity, "dataset_sha256": fingerprint(identity)}
