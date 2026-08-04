from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import gzip
import json
from pathlib import Path
import tempfile
import unittest

from openpyxl import Workbook
import polars as pl

from app.bloomberg_client import BloombergPullResult
from app.root_config import ROOT_COLUMNS, UPDATE_SHEET_NAME, load_root_config
from app.update_pipeline import (
    UpdatePaths,
    build_contract_universe,
    normalize_bloomberg_rows,
    run_bloomberg_update,
)


ROOT_ROWS = (
    (True, "WU", "GC Jet", "Comdty", "cpg", 7.45, 42, "{root}{month_code}{yy} {yellow_key}", "NYMEX:WU", "ME", "Refined", 1),
    (True, "HO", "Heating Oil", "Index", "$/gal", 7.45, 42, "{root}{month_code}{y} {yellow_key}", "NYMEX:HO", "", "Refined", 2),
)


def write_config(path: Path, *, max_mb: float = 20.0) -> None:
    workbook = Workbook()
    roots = workbook.active
    roots.title = "Security Roots"
    roots.append(ROOT_COLUMNS)
    for row in ROOT_ROWS:
        roots.append(row)
    update = workbook.create_sheet(UPDATE_SHEET_NAME)
    update.append(("setting", "value", "description"))
    settings = (
        ("history_start", "2024-01-01"),
        ("contract_start_year", 2026),
        ("contract_end_year", 2026),
        ("contract_history_months", 24),
        ("reference_depth", 2),
        ("overlap_days", 7),
        ("fields", "PX_LAST,PX_CLOSE,PX_SETTLE,PX_FAIR_1430"),
        ("host", "localhost"),
        ("port", 8194),
        ("service", "//blp/refdata"),
        ("batch_size", 25),
        ("request_timeout_seconds", 30),
        ("standalone_max_mb", max_mb),
    )
    for key, value in settings:
        update.append((key, value, ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)


@dataclass
class FakeClient:
    calls: list[tuple]
    price: float = 200.123456789

    def fetch(self, requests, fields, *, batch_size, timeout_seconds):
        self.calls.append((tuple(requests), tuple(fields), batch_size, timeout_seconds))
        rows = []
        for index, request in enumerate(requests):
            value = self.price + index / 1000
            rows.append(
                {
                    "security": request.security,
                    "date": request.end_date,
                    "PX_LAST": value,
                    "PX_CLOSE": value - 0.1111111,
                    "PX_SETTLE": value + 0.2222222,
                    "PX_FAIR_1430": value - 0.3333333,
                }
            )
        return BloombergPullResult(tuple(rows), ())


class UpdatePipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config_path = self.root / "config" / "security_roots.xlsx"
        write_config(self.config_path)
        self.paths = UpdatePaths.under(self.root, self.config_path)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_spreadsheet_generates_wu_comdty_and_ho_index_tickers(self) -> None:
        config = load_root_config(self.config_path)
        specs = build_contract_universe(config, date(2026, 8, 3))
        tickers = {spec.ticker for spec in specs}
        self.assertIn("WUF26 Comdty", tickers)
        self.assertIn("WUZ26 Comdty", tickers)
        self.assertIn("HOF6 Index", tickers)
        self.assertIn("HOG6 Index", tickers)
        self.assertIn("HOZ6 Index", tickers)

    def test_default_workbook_generates_hog6_comdty(self) -> None:
        config = load_root_config("config/security_roots.xlsx")
        specs = build_contract_universe(config, date(2026, 8, 3))
        tickers = {spec.ticker for spec in specs}
        self.assertIn("HOG6 Comdty", tickers)

    def test_reference_is_derived_from_contract_and_observation_dates(self) -> None:
        config = load_root_config(self.config_path)
        spec = next(
            item
            for item in build_contract_universe(config, date(2026, 8, 3))
            if item.ticker == "WUH26 Comdty"
        )
        frame, warnings = normalize_bloomberg_rows(
            (
                {"security": spec.ticker, "date": date(2024, 9, 3), "PX_LAST": 1.1234567, "PX_CLOSE": 1.0, "PX_SETTLE": 1.0, "PX_FAIR_1430": 1.0},
                {"security": spec.ticker, "date": date(2025, 3, 3), "PX_LAST": 2.1234567, "PX_CLOSE": 2.0, "PX_SETTLE": 2.0, "PX_FAIR_1430": 2.0},
            ),
            (spec,),
            config.update.fields,
            reference_depth=2,
        )
        self.assertFalse(warnings)
        self.assertEqual(frame["reference"].to_list(), [2, 1])
        self.assertEqual(frame["PX_LAST"].to_list(), [1.12346, 2.12346])

    def test_fake_update_writes_csv_gzip_parquet_html_and_manifest(self) -> None:
        client = FakeClient([])
        summary = run_bloomberg_update(
            paths=self.paths,
            client=client,
            as_of=date(2026, 8, 3),
            full=True,
        )
        self.assertTrue(summary["ok"])
        self.assertTrue(client.calls)
        self.assertTrue(self.paths.csv.exists())
        self.assertTrue(self.paths.csv_gzip.exists())
        self.assertTrue(self.paths.parquet.exists())
        self.assertTrue(self.paths.html.exists())
        self.assertTrue(self.paths.embedded_js.exists())
        self.assertTrue(self.paths.manifest.exists())

        csv_frame = pl.read_csv(self.paths.csv, try_parse_dates=True)
        parquet_frame = pl.read_parquet(self.paths.parquet)
        self.assertEqual(csv_frame.height, parquet_frame.height)
        self.assertEqual(set(csv_frame["security_prefix"]), {"WU", "HO"})
        self.assertEqual(set(csv_frame["CLEAN_NAME"]), {"GC Jet", "Heating Oil"})
        self.assertEqual(set(parquet_frame["CLEAN_NAME"]), {"GC Jet", "Heating Oil"})
        self.assertTrue(
            csv_frame.select((pl.col("PX_LAST") == pl.col("PX_LAST").round(5)).all()).item()
        )
        with gzip.open(self.paths.csv_gzip, "rt", encoding="utf-8") as handle:
            self.assertEqual(handle.readline().strip(), ",".join(csv_frame.columns))

        html = self.paths.html.read_text(encoding="utf-8")
        self.assertEqual(html.count('id="embedded-data"'), 1)
        self.assertNotIn('id="embedded-data-raw"', html)
        manifest = json.loads(self.paths.manifest.read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "complete")
        self.assertEqual(manifest["source"], "Bloomberg Desktop API")
        self.assertEqual(manifest["retained_rows"], csv_frame.height)
        self.assertEqual(set(manifest["root_coverage"]), {"WU", "HO"})

    def test_incremental_update_preserves_expired_history_and_replaces_overlap(self) -> None:
        first = FakeClient([], price=100.000001)
        run_bloomberg_update(
            paths=self.paths,
            client=first,
            as_of=date(2026, 8, 2),
            full=True,
        )
        before = pl.read_csv(self.paths.csv, try_parse_dates=True)
        second = FakeClient([], price=300.999999)
        run_bloomberg_update(
            paths=self.paths,
            client=second,
            as_of=date(2026, 8, 3),
            full=False,
        )
        after = pl.read_csv(self.paths.csv, try_parse_dates=True)
        self.assertGreaterEqual(after.height, before.height)
        requested = {request.security for request in second.calls[0][0]}
        self.assertTrue(requested)
        self.assertTrue(
            all(
                (ticker.startswith("WU") and ticker.endswith("26 Comdty"))
                or (ticker.startswith("HO") and ticker.endswith("6 Index"))
                for ticker in requested
            )
        )
        self.assertGreater(float(after["PX_LAST"].max()), 300.0)

    def test_late_export_failure_preserves_every_previous_artifact(self) -> None:
        sentinel = b"previous-good-artifact"
        tracked = (
            self.paths.csv,
            self.paths.csv_gzip,
            self.paths.parquet,
            self.paths.html,
            self.paths.embedded_js,
            self.paths.manifest,
        )
        for path in tracked:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(sentinel)
        write_config(self.config_path, max_mb=0.00001)

        with self.assertRaises(Exception):
            run_bloomberg_update(
                paths=self.paths,
                client=FakeClient([]),
                as_of=date(2026, 8, 3),
                full=True,
            )
        for path in tracked:
            self.assertEqual(path.read_bytes(), sentinel)


if __name__ == "__main__":
    unittest.main()
