"""Optional XLSX presentation layer; audited numbers are calculated elsewhere."""
from __future__ import annotations

import json
from fractions import Fraction
from io import BytesIO
from pathlib import Path


class ExcelDependencyError(RuntimeError):
    pass


SUMMARY_HEADERS = (
    "Inventory_ID", "OEM", "Nombre_pieza", "Marca", "Modelo", "Version", "Stock_interno",
    "Anuncios_observados", "Unidades_observadas", "Min_observado_EUR", "Max_observado_EUR",
    "P_inferior", "P_superior", "Corte_inferior_EUR", "Corte_superior_EUR", "Muestra_minima",
    "Anuncios_filtrados", "Unidades_filtradas", "Min_filtrado_EUR", "Max_filtrado_EUR",
    "Estado_evidencia", "Cobertura_completa",
)
AUDIT_HEADERS = (
    "Inventory_ID", "OEM_inventario", "Fila_auditoria", "Marketplace", "Offer_ID", "OEM_oferta",
    "Nombre_pieza", "Marca", "Modelo", "Version", "Unidades_anunciadas", "Precio_original_centimos",
    "Precio_EUR", "Fecha_observacion", "OEM_match", "Compatibilidad", "Incluida_observados",
    "Incluida_filtrados", "Motivos_exclusion", "Estado_filtro", "Duplicada_de_fila",
    "Oferta_original_JSON", "Evidencia_matching_JSON",
)


def _eur(value):
    if value is None:
        return None
    if isinstance(value, dict):
        value = Fraction(value["numerator"], value["denominator"])
    return float(Fraction(value) / 100)


def _append(sheet, values):
    sheet.append(values)
    for cell in sheet[sheet.max_row]:
        # Preserve leading zeros and literal formula-like data as text.
        if isinstance(cell.value, str):
            cell.data_type = "s"
            cell.number_format = "@"


def export_statistics_excel(path: Path, result: dict) -> None:
    """Serialize fully in memory, then exclusively create a new .xlsx file."""
    path = Path(path)
    if path.suffix.lower() != ".xlsx":
        raise ValueError("Excel output must use the .xlsx extension")
    if path.exists():
        raise FileExistsError(f"Excel output already exists (no overwrite): {path}")
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font
    except ImportError as exc:
        raise ExcelDependencyError('Excel requires the optional extra: python -m pip install -e ".[excel]"') from exc
    workbook = Workbook()
    try:
        summary = workbook.active
        summary.title = "Resumen"
        offers = workbook.create_sheet("Ofertas")
        trace = workbook.create_sheet("Traza")
        metadata = workbook.create_sheet("Configuracion")
        _append(summary, SUMMARY_HEADERS)
        _append(offers, AUDIT_HEADERS)
        _append(trace, ("Categoria", "Paso", "Operacion", "Argumentos_JSON", "SQL", "Parametros_JSON",
                        "Resultados", "Estado", "Error_JSON"))
        _append(metadata, ("Parametro", "Valor"))
        config = result["statistics_config"]
        for case in result["results"]:
            item, stats = case["inventory"], case["statistics"]
            _append(summary, (
                item["id"], item["oem"], item["part_name"], item["brand"], item["model"], item["version"], item["stock_qty"],
                stats["observed_offers_count"], stats["observed_units"], _eur(stats["observed_min_price_cents"]),
                _eur(stats["observed_max_price_cents"]), float(config["lower_percentile"]), float(config["upper_percentile"]),
                _eur(stats["lower_cutoff_cents"]), _eur(stats["upper_cutoff_cents"]), config["min_sample_size"],
                stats["filtered_offers_count"], stats["filtered_units"], _eur(stats["filtered_min_price_cents"]),
                _eur(stats["filtered_max_price_cents"]), stats["evidence_status"], stats["coverage_complete"],
            ))
        audit_rows = [(case["inventory"], row) for case in result["results"] for row in case["offer_audit"]]
        audit_rows += [(None, row) for row in result["unassociated_offer_audit"]]
        for item, row in audit_rows:
            offer = row["offer"]
            price = offer.get("price_cents")
            _append(offers, (
                item["id"] if item else None, item["oem"] if item else None, row["audit_row"],
                row["source_marketplace_id"], offer.get("offer_id"), offer.get("oem"), offer.get("part_name"),
                offer.get("brand"), offer.get("model"), offer.get("version"), offer.get("available_units"), price,
                _eur(price) if type(price) is int else None, offer.get("observed_on"), row["oem_match"],
                row["compatibility"], row["observed_included"], row["filtered_included"],
                "; ".join(row["exclusion_reasons"]), row["filter_reason"], row["duplicate_of"],
                json.dumps(offer, ensure_ascii=False), json.dumps(row["field_evidence"], ensure_ascii=False),
            ))
        for event in result["trace"]:
            _append(trace, ("sql", event["step"], event["tool"], json.dumps(event["args"], ensure_ascii=False),
                            event.get("sql"), json.dumps(event.get("parameters"), ensure_ascii=False),
                            event["result_count"], event.get("status"), json.dumps(event.get("error"), ensure_ascii=False)))
        for event in result["statistics_trace"]:
            _append(trace, ("statistics", event["step"], event["operation"], json.dumps(event, ensure_ascii=False),
                            None, None, event["observed_offers_count"], event["evidence_status"], None))
        for key, value in {
            "data_origin": result["data_origin"], "demo_only": result["demo_only"], "delivery": result["delivery"],
            "read_status": result["status"], "read_coverage_complete": result["complete"],
            "audit_scope": result["audit_scope"], "audit_complete": result["audit_complete"],
            "source": json.dumps(result["source"], ensure_ascii=False),
            "selection": json.dumps(result["selection"]), **config,
            "interpretation": "Demonstrative sample threshold; no real statistical validation or pricing decision",
            "deduplication": "Per inventory: (marketplace_id, offer_id); cross-source physical stock is not inferred",
        }.items():
            _append(metadata, (key, value))
        for sheet in workbook:
            sheet.freeze_panes = "A2"
            sheet.auto_filter.ref = sheet.dimensions
            for cell in sheet[1]:
                cell.font = Font(bold=True)
                sheet.column_dimensions[cell.column_letter].width = min(36, max(16, len(str(cell.value)) + 2))
            for column, header in enumerate(next(sheet.values), start=1):
                if str(header).endswith("_EUR"):
                    for cells in sheet.iter_rows(min_row=2, min_col=column, max_col=column):
                        cells[0].number_format = "0.0000" if str(header).startswith("Corte_") else "0.00"
        with BytesIO() as buffer:
            workbook.save(buffer)
            content = buffer.getvalue()
    finally:
        workbook.close()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic exclusive creation protects existing files, including races.
    with path.open("xb") as output:
        output.write(content)
