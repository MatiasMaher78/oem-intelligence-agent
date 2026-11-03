"""Optional Excel presentation: values, evidence, text identity and no overwrite."""
import copy
import importlib.util
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from oem_agent.excel_export import ExcelDependencyError, export_statistics_excel
from oem_agent.stage3_fixtures import seed_statistics_demo
from oem_agent.stage3_statistics import execute_stage2_statistics

HAS_EXCEL = importlib.util.find_spec("openpyxl") is not None


class OptionalExcelDependencyTests(unittest.TestCase):
    def test_core_analysis_works_when_excel_dependency_is_unavailable(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "fixture.sqlite"
            seed_statistics_demo(path)
            target = Path(temporary) / "report.xlsx"
            with patch.dict(sys.modules, {"openpyxl": None}):
                result = execute_stage2_statistics(path)
                self.assertEqual(len(result["results"]), 27)
                with self.assertRaisesRegex(ExcelDependencyError, "optional extra"):
                    export_statistics_excel(target, result)
            self.assertFalse(target.exists())


@unittest.skipUnless(HAS_EXCEL, "Install the optional [excel] extra to verify XLSX presentation")
class ExcelExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "statistics.sqlite"
        self.output = Path(self.tmp.name) / "report.xlsx"
        seed_statistics_demo(self.path)
        self.result = execute_stage2_statistics(self.path)

    def load(self, path=None):
        from openpyxl import load_workbook
        book = load_workbook(path or self.output, read_only=False, data_only=False)
        self.addCleanup(book.close)
        return book

    def rows(self, sheet):
        values = list(sheet.values)
        return [dict(zip(values[0], row)) for row in values[1:]]

    def summary_row(self, book, identifier):
        return next(row for row in self.rows(book["Resumen"]) if row["Inventory_ID"] == identifier)

    def test_summary_has_all_pieces_observed_filtered_values_and_units(self):
        export_statistics_excel(self.output, self.result)
        book = self.load()
        self.assertEqual(book.sheetnames, ["Resumen", "Ofertas", "Traza", "Configuracion"])
        self.assertEqual(len(self.rows(book["Resumen"])), 27)
        row = self.summary_row(book, 10)
        self.assertEqual((row["Anuncios_observados"], row["Unidades_observadas"]), (10, 55))
        self.assertEqual((row["Min_observado_EUR"], row["Max_observado_EUR"]), (10, 100))
        self.assertEqual((row["P_inferior"], row["P_superior"]), (10, 90))
        self.assertEqual((row["Corte_inferior_EUR"], row["Corte_superior_EUR"]), (19, 91))
        self.assertEqual((row["Anuncios_filtrados"], row["Unidades_filtradas"]), (8, 44))
        self.assertEqual((row["Min_filtrado_EUR"], row["Max_filtrado_EUR"]), (20, 90))
        self.assertEqual(row["Estado_evidencia"], "sufficient_sample_demo")
        self.assertEqual(len(self.rows(book["Traza"])), 56 + 27)

    def test_offer_sheet_keeps_outliers_rejections_and_unrelated_source_rows(self):
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("""INSERT INTO stage2_market_offers VALUES
                ('DEMO-MARKET-A', 'UNRELATED', 'DEMO-UNRELATED', 'Otra pieza', 'MarcaDemo-Z',
                 'ModeloDemo-Z', 'VersionDemo-Z', 3, 7777, '2026-11-03')""")
        result = execute_stage2_statistics(self.path)
        export_statistics_excel(self.output, result)
        rows = self.rows(self.load()["Ofertas"])
        expected = sum(len(case["offer_audit"]) for case in result["results"]) + 1
        self.assertEqual(len(rows), expected)
        for identifier, price in (("STATS-01", 1000), ("STATS-10", 10000)):
            row = next(row for row in rows if row["Inventory_ID"] == 10 and row["Offer_ID"] == identifier)
            self.assertTrue(row["Incluida_observados"])
            self.assertFalse(row["Incluida_filtrados"])
            self.assertEqual(row["Precio_original_centimos"], price)
            self.assertEqual(row["Estado_filtro"], "outside_percentile_band")
        orphan = next(row for row in rows if row["Offer_ID"] == "UNRELATED")
        self.assertIsNone(orphan["Inventory_ID"])
        self.assertFalse(orphan["Incluida_observados"])
        self.assertEqual(json.loads(orphan["Oferta_original_JSON"])["price_cents"], 7777)
        source_ids = {(row["marketplace_id"], row["offer_id"]) for row in result["source_offers"]}
        self.assertEqual({(row["Marketplace"], row["Offer_ID"]) for row in rows}, source_ids)

    def test_absent_values_remain_blank_and_zero_remains_numeric_zero(self):
        export_statistics_excel(self.output, self.result)
        book = self.load()
        zero = self.summary_row(book, 7)
        self.assertEqual(zero["Unidades_observadas"], 0)
        self.assertEqual(zero["Anuncios_observados"], 0)
        self.assertIsNone(zero["Min_observado_EUR"])
        self.assertIsNone(zero["Unidades_filtradas"])
        insufficient = self.summary_row(book, 2)
        self.assertEqual(insufficient["Estado_evidencia"], "insufficient_sample")
        self.assertIsNone(insufficient["Min_filtrado_EUR"])
        self.assertIsNone(insufficient["Corte_inferior_EUR"])
        offer = next(row for row in self.rows(book["Ofertas"])
                     if row["Inventory_ID"] == 7 and row["Offer_ID"] == "002")
        self.assertEqual(offer["Unidades_anunciadas"], 0)
        self.assertIn("no_available_units", offer["Motivos_exclusion"])

    def test_oem_leading_zeros_and_formula_like_text_are_preserved_as_text(self):
        result = copy.deepcopy(self.result)
        identifier = "000001234567890123456789"
        result["results"][0]["inventory"]["oem"] = identifier
        result["results"][0]["inventory"]["part_name"] = "=1+1"
        result["results"][0]["offer_audit"][0]["offer"]["oem"] = "=2+2"
        export_statistics_excel(self.output, result)
        book = self.load()
        self.assertEqual(book["Resumen"]["B2"].value, identifier)
        self.assertEqual(book["Resumen"]["B2"].data_type, "s")
        self.assertEqual(book["Resumen"]["B2"].number_format, "@")
        self.assertEqual(book["Resumen"]["C2"].value, "=1+1")
        self.assertEqual(book["Ofertas"]["F2"].value, "=2+2")
        self.assertFalse(any(cell.data_type == "f" for sheet in book for row in sheet for cell in row))

    def test_existing_file_and_concurrent_creation_are_never_overwritten(self):
        from openpyxl import Workbook
        self.output.write_bytes(b"existing file")
        with self.assertRaises(FileExistsError):
            export_statistics_excel(self.output, self.result)
        self.assertEqual(self.output.read_bytes(), b"existing file")
        racing = Path(self.tmp.name) / "racing.xlsx"
        save = Workbook.save
        def create_competing_file(workbook, stream):
            racing.write_bytes(b"concurrently created")
            return save(workbook, stream)
        with patch.object(Workbook, "save", create_competing_file), self.assertRaises(FileExistsError):
            export_statistics_excel(racing, self.result)
        self.assertEqual(racing.read_bytes(), b"concurrently created")

    def test_serialization_error_does_not_create_a_partial_output(self):
        from openpyxl import Workbook
        with patch.object(Workbook, "save", side_effect=RuntimeError("serialization failed")):
            with self.assertRaisesRegex(RuntimeError, "serialization failed"):
                export_statistics_excel(self.output, self.result)
        self.assertFalse(self.output.exists())

    def test_repeated_exports_have_identical_cell_values_and_types(self):
        other = Path(self.tmp.name) / "second.xlsx"
        export_statistics_excel(self.output, self.result)
        export_statistics_excel(other, self.result)
        first, second = self.load(), self.load(other)
        for name in first.sheetnames:
            values = lambda sheet: [[(cell.value, cell.data_type, cell.number_format) for cell in row] for row in sheet]
            self.assertEqual(values(first[name]), values(second[name]))

    def test_partial_audit_coverage_is_visible_and_filtered_prices_are_absent(self):
        with patch("oem_agent.stage3_statistics._list_source_offers", side_effect=sqlite3.OperationalError("audit failed")):
            result = execute_stage2_statistics(self.path)
        export_statistics_excel(self.output, result)
        book = self.load()
        row = self.summary_row(book, 10)
        self.assertEqual(row["Estado_evidencia"], "source_error")
        self.assertIsNone(row["Min_filtrado_EUR"])
        metadata = {row["Parametro"]: row["Valor"] for row in self.rows(book["Configuracion"])}
        self.assertFalse(metadata["audit_complete"])
        self.assertEqual(metadata["read_status"], "partial")

    def test_empty_selection_still_exports_headers_and_source_offer_audit(self):
        result = execute_stage2_statistics(self.path, min_days=100, max_days=110)
        export_statistics_excel(self.output, result)
        book = self.load()
        self.assertEqual(book["Resumen"].max_row, 1)
        self.assertEqual(len(self.rows(book["Ofertas"])), 15)

    def test_invalid_extension_is_rejected_before_writing(self):
        path = Path(self.tmp.name) / "report.csv"
        with self.assertRaises(ValueError):
            export_statistics_excel(path, self.result)
        self.assertFalse(path.exists())

    def test_cli_exports_workbook_and_refuses_to_overwrite_it(self):
        command = [sys.executable, "-m", "oem_agent.stage2", "stats", "--db", str(self.path), "--excel", str(self.output)]
        cwd = Path(__file__).resolve().parents[1]
        success = subprocess.run(command, cwd=cwd, text=True, capture_output=True)
        self.assertEqual(success.returncode, 0, success.stderr)
        self.assertEqual(json.loads(success.stdout)["delivery"], "stage2-03")
        self.assertEqual(self.summary_row(self.load(), 10)["Anuncios_filtrados"], 8)
        before = self.output.read_bytes()
        rejected = subprocess.run(command, cwd=cwd, text=True, capture_output=True)
        self.assertEqual(rejected.returncode, 2)
        self.assertEqual(rejected.stdout, "")
        self.assertEqual(self.output.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
