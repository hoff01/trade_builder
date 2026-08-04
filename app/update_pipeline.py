"""Transactional Bloomberg-to-CSV-to-portable-dashboard update pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Iterable, Protocol, Sequence

import polars as pl

from app.export_single_file import SUPPORTED_PRICE_FIELDS, export_dashboard
from app.root_config import RootConfig, SecurityRoot, load_root_config


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "security_roots.xlsx"
DEFAULT_CSV = PROJECT_ROOT / "data" / "pricing_history.csv"
DEFAULT_CSV_GZIP = PROJECT_ROOT / "dist" / "pricing_data.csv.gz"
DEFAULT_PARQUET = PROJECT_ROOT / "dist" / "pricing_data.parquet"
DEFAULT_HTML = PROJECT_ROOT / "dist" / "pricing_dashboard_trade_builder.html"
DEFAULT_EMBEDDED_JS = PROJECT_ROOT / "app" / "static" / "embedded_data.js"
DEFAULT_MANIFEST = PROJECT_ROOT / "dist" / "update_manifest.json"
PRECISION = 5

MONTH_CODES = {
    1: ("F", "Jan"),
    2: ("G", "Feb"),
    3: ("H", "Mar"),
    4: ("J", "Apr"),
    5: ("K", "May"),
    6: ("M", "Jun"),
    7: ("N", "Jul"),
    8: ("Q", "Aug"),
    9: ("U", "Sep"),
    10: ("V", "Oct"),
    11: ("X", "Nov"),
    12: ("Z", "Dec"),
}

METADATA_COLUMNS = (
    "date",
    "security_str",
    "FUT_CUR_GEN_TICKER",
    "security_prefix",
    "CLEAN_NAME",
    "frequency",
    "reference",
    "month",
    "contract_month_yr",
    "contract_year",
)
TRAILING_COLUMNS = (
    "year",
    "bbl_per_mt",
    "gal_per_bbl",
    "native_unit",
    "yellow_key",
)


class UpdateError(RuntimeError):
    """Raised when an update cannot be published safely."""


@dataclass(frozen=True)
class ContractSpec:
    ticker: str
    root: str
    display_name: str
    yellow_key: str
    native_unit: str
    bbl_per_mt: float
    gal_per_bbl: float
    month_number: int
    month_code: str
    month_label: str
    contract_year: int
    contract_month_yr: str
    start_date: date
    end_date: date


@dataclass(frozen=True)
class UpdatePaths:
    project_root: Path = PROJECT_ROOT
    config: Path = DEFAULT_CONFIG
    csv: Path = DEFAULT_CSV
    csv_gzip: Path = DEFAULT_CSV_GZIP
    parquet: Path = DEFAULT_PARQUET
    html: Path = DEFAULT_HTML
    embedded_js: Path = DEFAULT_EMBEDDED_JS
    manifest: Path = DEFAULT_MANIFEST

    @classmethod
    def under(cls, root: str | Path, config: str | Path) -> "UpdatePaths":
        base = Path(root).resolve()
        return cls(
            project_root=base,
            config=Path(config).resolve(),
            csv=base / "data" / "pricing_history.csv",
            csv_gzip=base / "dist" / "pricing_data.csv.gz",
            parquet=base / "dist" / "pricing_data.parquet",
            html=base / "dist" / "pricing_dashboard_trade_builder.html",
            embedded_js=base / "app" / "static" / "embedded_data.js",
            manifest=base / "dist" / "update_manifest.json",
        )


class HistoricalClient(Protocol):
    def fetch(
        self,
        requests: Sequence[Any],
        fields: Sequence[str],
        *,
        batch_size: int,
        timeout_seconds: int,
    ) -> Any: ...


def _canonical_columns(fields: Sequence[str]) -> tuple[str, ...]:
    return METADATA_COLUMNS + tuple(fields) + TRAILING_COLUMNS


def _canonical_schema(fields: Sequence[str]) -> dict[str, pl.DataType]:
    schema: dict[str, pl.DataType] = {
        "date": pl.Date,
        "security_str": pl.Utf8,
        "FUT_CUR_GEN_TICKER": pl.Utf8,
        "security_prefix": pl.Utf8,
        "CLEAN_NAME": pl.Utf8,
        "frequency": pl.Utf8,
        "reference": pl.Int64,
        "month": pl.Utf8,
        "contract_month_yr": pl.Utf8,
        "contract_year": pl.Int64,
    }
    schema.update({field: pl.Float64 for field in fields})
    schema.update(
        {
            "year": pl.Int64,
            "bbl_per_mt": pl.Float64,
            "gal_per_bbl": pl.Float64,
            "native_unit": pl.Utf8,
            "yellow_key": pl.Utf8,
        }
    )
    return schema


def _empty_frame(fields: Sequence[str]) -> pl.DataFrame:
    return pl.DataFrame(schema=_canonical_schema(fields))


def _subtract_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 - int(months)
    year, zero_based_month = divmod(month_index, 12)
    return date(year, zero_based_month + 1, 1)


def _ticker_for(root: SecurityRoot, month_code: str, contract_year: int) -> str:
    values = {
        "root": root.root,
        "month_code": month_code,
        "yy": f"{contract_year % 100:02d}",
        "year_2d": f"{contract_year % 100:02d}",
        "year": contract_year,
        "yellow_key": root.yellow_key,
    }
    try:
        ticker = root.ticker_template.format(**values)
    except (KeyError, ValueError) as exc:
        raise UpdateError(f"Cannot render ticker_template for {root.root}: {exc}") from exc
    return " ".join(ticker.split())


def build_contract_universe(config: RootConfig, as_of: date | None = None) -> tuple[ContractSpec, ...]:
    """Expand enabled spreadsheet roots into dated Bloomberg contracts."""

    current_date = as_of or date.today()
    settings = config.update
    specs: list[ContractSpec] = []
    seen: set[str] = set()
    for root in sorted(config.enabled_roots, key=lambda item: (item.sort_order, item.root)):
        for contract_year in range(settings.contract_start_year, settings.contract_end_year + 1):
            for month_number, (month_code, month_label) in MONTH_CODES.items():
                delivery_start = date(contract_year, month_number, 1)
                start_date = max(
                    settings.history_start,
                    _subtract_months(delivery_start, settings.contract_history_months),
                )
                end_date = min(current_date, delivery_start - timedelta(days=1))
                ticker = _ticker_for(root, month_code, contract_year)
                normalized = ticker.casefold()
                if normalized in seen:
                    raise UpdateError(f"Ticker template generated a duplicate security: {ticker}")
                seen.add(normalized)
                specs.append(
                    ContractSpec(
                        ticker=ticker,
                        root=root.root,
                        display_name=root.display_name,
                        yellow_key=root.yellow_key,
                        native_unit=root.native_unit,
                        bbl_per_mt=root.bbl_per_mt,
                        gal_per_bbl=root.gal_per_bbl,
                        month_number=month_number,
                        month_code=month_code,
                        month_label=month_label,
                        contract_year=contract_year,
                        contract_month_yr=f"{month_code}{contract_year % 100:02d}",
                        start_date=start_date,
                        end_date=end_date,
                    )
                )
    return tuple(specs)


def _parse_observation_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _finite_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return round(parsed, PRECISION) if math.isfinite(parsed) else None


def _reference_for(observation_date: date, spec: ContractSpec) -> int:
    nearest_delivery_year = observation_date.year + (
        1 if observation_date.month >= spec.month_number else 0
    )
    return spec.contract_year - nearest_delivery_year + 1


def normalize_bloomberg_rows(
    rows: Iterable[dict[str, object]],
    specs: Sequence[ContractSpec],
    fields: Sequence[str],
    reference_depth: int,
) -> tuple[pl.DataFrame, tuple[str, ...]]:
    """Convert Bloomberg response rows into the stable shareable CSV contract."""

    by_ticker = {spec.ticker.casefold(): spec for spec in specs}
    normalized_rows: list[dict[str, object]] = []
    skipped_unknown: set[str] = set()
    skipped_reference = 0
    skipped_missing_last = 0
    for raw in rows:
        security = str(raw.get("security") or raw.get("security_str") or "").strip()
        spec = by_ticker.get(security.casefold())
        if spec is None:
            if security:
                skipped_unknown.add(security)
            continue
        observation_date = _parse_observation_date(raw.get("date"))
        if observation_date is None or observation_date < spec.start_date or observation_date > spec.end_date:
            continue
        reference = _reference_for(observation_date, spec)
        if reference < 1 or reference > reference_depth:
            skipped_reference += 1
            continue
        values = {field: _finite_float(raw.get(field)) for field in fields}
        if values.get("PX_LAST") is None:
            skipped_missing_last += 1
            continue
        record: dict[str, object] = {
            "date": observation_date,
            "security_str": spec.ticker,
            "FUT_CUR_GEN_TICKER": spec.ticker,
            "security_prefix": spec.root,
            "CLEAN_NAME": spec.display_name,
            "frequency": "Monthly",
            "reference": reference,
            "month": spec.month_label,
            "contract_month_yr": spec.contract_month_yr,
            "contract_year": spec.contract_year,
            **values,
            "year": observation_date.year,
            "bbl_per_mt": round(spec.bbl_per_mt, PRECISION),
            "gal_per_bbl": round(spec.gal_per_bbl, PRECISION),
            "native_unit": spec.native_unit,
            "yellow_key": spec.yellow_key,
        }
        normalized_rows.append(record)

    warnings: list[str] = []
    if skipped_unknown:
        sample = ", ".join(sorted(skipped_unknown)[:5])
        warnings.append(f"Ignored {len(skipped_unknown)} unrequested Bloomberg securities: {sample}")
    if skipped_reference:
        warnings.append(
            f"Ignored {skipped_reference} observations outside reference depth {reference_depth}."
        )
    if skipped_missing_last:
        warnings.append(f"Ignored {skipped_missing_last} observations without PX_LAST.")

    if not normalized_rows:
        return _empty_frame(fields), tuple(warnings)
    frame = pl.DataFrame(
        normalized_rows,
        schema=_canonical_schema(fields),
        strict=False,
    )
    frame = frame.unique(subset=["date", "security_str"], keep="last", maintain_order=True)
    return _round_frame(frame, fields), tuple(warnings)


def _spec_metadata_frame(specs: Sequence[ContractSpec]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "security_str": spec.ticker,
                "FUT_CUR_GEN_TICKER": spec.ticker,
                "security_prefix": spec.root,
                "CLEAN_NAME": spec.display_name,
                "frequency": "Monthly",
                "month": spec.month_label,
                "contract_month_yr": spec.contract_month_yr,
                "contract_year": spec.contract_year,
                "bbl_per_mt": spec.bbl_per_mt,
                "gal_per_bbl": spec.gal_per_bbl,
                "native_unit": spec.native_unit,
                "yellow_key": spec.yellow_key,
                "_delivery_month": spec.month_number,
                "_start_date": spec.start_date,
                "_end_date": spec.end_date,
            }
            for spec in specs
        ]
    )


def _normalize_existing_frame(
    path: Path,
    specs: Sequence[ContractSpec],
    fields: Sequence[str],
    reference_depth: int,
) -> pl.DataFrame:
    if not path.exists() or not path.stat().st_size:
        return _empty_frame(fields)
    try:
        source = pl.read_csv(path, try_parse_dates=True, infer_schema_length=10_000)
    except Exception as exc:
        raise UpdateError(f"The existing CSV cannot be read: {path}: {exc}") from exc
    if "date" not in source.columns or "security_str" not in source.columns:
        raise UpdateError("The existing CSV is missing date or security_str.")
    if source["date"].dtype == pl.Datetime:
        source = source.with_columns(pl.col("date").cast(pl.Date))
    elif source["date"].dtype != pl.Date:
        source = source.with_columns(
            pl.col("date").cast(pl.Utf8).str.to_date("%Y-%m-%d", strict=False)
        )
    for field in fields:
        if field not in source.columns:
            source = source.with_columns(pl.lit(None, dtype=pl.Float64).alias(field))
    source = source.select(
        ["date", "security_str"]
        + [pl.col(field).cast(pl.Float64, strict=False).alias(field) for field in fields]
    )
    source = source.join(_spec_metadata_frame(specs), on="security_str", how="inner")
    nearest_delivery_year = (
        pl.col("date").dt.year()
        + (pl.col("date").dt.month() >= pl.col("_delivery_month")).cast(pl.Int64)
    )
    source = source.with_columns(
        (pl.col("contract_year") - nearest_delivery_year + 1).alias("reference"),
        pl.col("date").dt.year().alias("year"),
    ).filter(
        pl.col("date").is_not_null()
        & (pl.col("date") >= pl.col("_start_date"))
        & (pl.col("date") <= pl.col("_end_date"))
        & pl.col("reference").is_between(1, reference_depth)
        & pl.col("PX_LAST").is_not_null()
    )
    source = source.select(_canonical_columns(fields))
    source = source.unique(subset=["date", "security_str"], keep="last", maintain_order=True)
    return _round_frame(source, fields)


def _round_frame(frame: pl.DataFrame, fields: Sequence[str]) -> pl.DataFrame:
    float_columns = [
        column
        for column in (*fields, "bbl_per_mt", "gal_per_bbl")
        if column in frame.columns
    ]
    if float_columns:
        frame = frame.with_columns(
            [pl.col(column).cast(pl.Float64, strict=False).round(PRECISION) for column in float_columns]
        )
    return frame.select(_canonical_columns(fields))


def _build_requests(
    specs: Sequence[ContractSpec],
    existing: pl.DataFrame,
    overlap_days: int,
    as_of: date,
    full: bool,
) -> list[Any]:
    from app.bloomberg_client import HistoricalRequest

    last_by_ticker: dict[str, date] = {}
    if not full and existing.height:
        last_by_ticker = {
            str(security): last_date
            for security, last_date in existing.group_by("security_str")
            .agg(pl.col("date").max().alias("last_date"))
            .iter_rows()
        }

    requests: list[HistoricalRequest] = []
    for spec in specs:
        if spec.start_date > spec.end_date:
            continue
        last_date = last_by_ticker.get(spec.ticker)
        if last_date and spec.end_date < as_of:
            continue
        if last_date and last_date >= spec.end_date:
            continue
        start_date = spec.start_date
        if last_date:
            start_date = max(start_date, last_date - timedelta(days=overlap_days))
        requests.append(HistoricalRequest(spec.ticker, start_date, spec.end_date))
    return requests


def _merge_frames(existing: pl.DataFrame, pulled: pl.DataFrame, fields: Sequence[str]) -> pl.DataFrame:
    frames = [frame for frame in (existing, pulled) if frame.height]
    if not frames:
        return _empty_frame(fields)
    combined = pl.concat(frames, how="vertical_relaxed")
    combined = combined.unique(
        subset=["date", "security_str"], keep="last", maintain_order=True
    )
    return _round_frame(
        combined.sort(["security_prefix", "security_str", "date"]), fields
    )


def validate_canonical_frame(
    frame: pl.DataFrame,
    config: RootConfig,
    specs: Sequence[ContractSpec],
    as_of: date | None = None,
) -> None:
    issues: list[str] = []
    required = set(_canonical_columns(config.update.fields))
    missing = sorted(required - set(frame.columns))
    if missing:
        issues.append("missing canonical columns: " + ", ".join(missing))
    if not frame.height:
        issues.append("Bloomberg returned no usable rows.")
    if issues:
        raise UpdateError("Update data validation failed:\n- " + "\n- ".join(issues))

    validation_date = as_of or date.today()
    if frame.filter(pl.col("date") > validation_date).height:
        issues.append("rows contain future observation dates")
    duplicate_count = (
        frame.group_by(["date", "security_str"])
        .len()
        .filter(pl.col("len") > 1)
        .height
    )
    if duplicate_count:
        issues.append(f"{duplicate_count} duplicate (date, security_str) keys")
    if frame.filter(pl.col("PX_LAST").is_null() | ~pl.col("PX_LAST").is_finite()).height:
        issues.append("PX_LAST contains null or non-finite values")

    expected_roots = set(config.enabled_root_codes)
    actual_roots = set(frame["security_prefix"].unique().to_list())
    missing_roots = sorted(expected_roots - actual_roots)
    extra_roots = sorted(actual_roots - expected_roots)
    if missing_roots:
        issues.append("enabled roots have no usable Bloomberg rows: " + ", ".join(missing_roots))
    if extra_roots:
        issues.append("unconfigured roots reached the canonical CSV: " + ", ".join(extra_roots))

    universe = {spec.ticker for spec in specs}
    unexpected = frame.filter(~pl.col("security_str").is_in(list(universe))).height
    if unexpected:
        issues.append(f"{unexpected} rows have securities outside the spreadsheet ticker universe")
    bad_reference = frame.filter(
        ~pl.col("reference").is_between(1, config.update.reference_depth)
    ).height
    if bad_reference:
        issues.append(f"{bad_reference} rows have an invalid dated-contract reference")

    precision_columns = list(config.update.fields) + ["bbl_per_mt", "gal_per_bbl"]
    scale = float(10**PRECISION)
    for column in precision_columns:
        excessive = frame.filter(
            pl.col(column).is_not_null()
            & (((pl.col(column) * scale) - (pl.col(column) * scale).round()).abs() > 1e-7)
        ).height
        if excessive:
            issues.append(f"{excessive} rows in {column} exceed {PRECISION} decimals")
    if issues:
        raise UpdateError("Update data validation failed:\n- " + "\n- ".join(issues))


def _run_validator(csv_path: Path, config_path: Path) -> str:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "validate_pricing_data.py"),
        str(csv_path),
        "--config",
        str(config_path),
        "--max-decimals",
        str(PRECISION),
    ]
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        timeout=300,
        check=False,
    )
    if completed.returncode:
        detail = (completed.stdout + "\n" + completed.stderr).strip()
        raise UpdateError(f"Staged CSV failed the repository validator:\n{detail}")
    return completed.stdout.strip()


def _write_csv_gzip(source: Path, output: Path) -> None:
    with source.open("rb") as input_handle, output.open("wb") as raw_output:
        with gzip.GzipFile(
            filename="",
            mode="wb",
            compresslevel=9,
            fileobj=raw_output,
            mtime=0,
        ) as gzip_output:
            shutil.copyfileobj(input_handle, gzip_output, length=1024 * 1024)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_revision() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            timeout=5,
            check=True,
        )
        return completed.stdout.strip()
    except Exception:
        return "unknown"


def _relative_display(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path.resolve())


def _root_stats(frame: pl.DataFrame) -> dict[str, dict[str, object]]:
    stats = (
        frame.group_by("security_prefix")
        .agg(
            pl.len().alias("rows"),
            pl.col("security_str").n_unique().alias("securities"),
            pl.col("date").min().alias("min_date"),
            pl.col("date").max().alias("max_date"),
        )
        .sort("security_prefix")
    )
    result: dict[str, dict[str, object]] = {}
    for row in stats.iter_rows(named=True):
        result[str(row["security_prefix"])] = {
            "rows": int(row["rows"]),
            "securities": int(row["securities"]),
            "min_date": row["min_date"].isoformat(),
            "max_date": row["max_date"].isoformat(),
        }
    return result


def _verify_parquet_parity(
    frame: pl.DataFrame, parquet_path: Path, fields: Sequence[str]
) -> None:
    parquet = pl.read_parquet(parquet_path)
    compare_columns = ["date", "security_str", "reference", *fields]
    if parquet.height != frame.height:
        raise UpdateError(
            f"CSV/Parquet row-count mismatch: {frame.height} CSV rows vs {parquet.height} Parquet rows."
        )
    left = frame.select(compare_columns).sort(["date", "security_str", "reference"])
    right = parquet.select(compare_columns).sort(["date", "security_str", "reference"])
    if not left.equals(right, null_equal=True):
        raise UpdateError("CSV and compact Parquet keys/prices are not identical.")


def _promote_artifacts(staged_to_target: Sequence[tuple[Path, Path]], backup_root: Path) -> None:
    backup_root.mkdir(parents=True, exist_ok=True)
    backups: dict[Path, Path | None] = {}
    promoted: list[Path] = []
    for index, (_staged, target) in enumerate(staged_to_target):
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            backup = backup_root / f"{index:02d}-{target.name}"
            shutil.copy2(target, backup)
            backups[target] = backup
        else:
            backups[target] = None
    try:
        for staged, target in staged_to_target:
            os.replace(staged, target)
            promoted.append(target)
    except Exception as exc:
        for target in reversed(promoted):
            backup = backups[target]
            if backup is None:
                target.unlink(missing_ok=True)
            else:
                os.replace(backup, target)
        raise UpdateError(f"Artifact promotion failed and was rolled back: {exc}") from exc


def run_bloomberg_update(
    *,
    paths: UpdatePaths | None = None,
    client: HistoricalClient | None = None,
    as_of: date | None = None,
    full: bool = False,
    run_repository_validator: bool = True,
) -> dict[str, Any]:
    """Pull Bloomberg data and publish one internally consistent export package."""

    output_paths = paths or UpdatePaths()
    output_paths.project_root.mkdir(parents=True, exist_ok=True)
    current_date = as_of or date.today()
    requested_at = datetime.now(timezone.utc).replace(microsecond=0)
    config = load_root_config(output_paths.config)
    specs = build_contract_universe(config, current_date)
    existing = (
        _empty_frame(config.update.fields)
        if full
        else _normalize_existing_frame(
            output_paths.csv,
            specs,
            config.update.fields,
            config.update.reference_depth,
        )
    )
    requests = _build_requests(
        specs,
        existing,
        config.update.overlap_days,
        current_date,
        full,
    )

    pull_rows: Iterable[dict[str, object]] = ()
    pull_warnings: list[str] = []
    if requests:
        if client is None:
            from app.bloomberg_client import BloombergClient

            client = BloombergClient(
                host=config.update.host,
                port=config.update.port,
                service=config.update.service,
            )
        result = client.fetch(
            requests,
            config.update.fields,
            batch_size=config.update.batch_size,
            timeout_seconds=config.update.request_timeout_seconds,
        )
        pull_rows = result.rows
        pull_warnings.extend(getattr(result, "warnings", ()))

    pulled, normalize_warnings = normalize_bloomberg_rows(
        pull_rows,
        specs,
        config.update.fields,
        config.update.reference_depth,
    )
    pull_warnings.extend(normalize_warnings)
    combined = _merge_frames(existing, pulled, config.update.fields)
    validate_canonical_frame(combined, config, specs, current_date)

    staging_parent = output_paths.project_root
    with tempfile.TemporaryDirectory(prefix=".pricing-update-", dir=staging_parent) as directory:
        staging = Path(directory)
        staged_csv = staging / "pricing_history.csv"
        staged_csv_gzip = staging / "pricing_data.csv.gz"
        staged_parquet = staging / "pricing_data.parquet"
        staged_html = staging / "pricing_dashboard_trade_builder.html"
        staged_embedded_js = staging / "embedded_data.js"
        staged_manifest = staging / "update_manifest.json"

        combined.write_csv(staged_csv, float_precision=PRECISION)
        if run_repository_validator:
            _run_validator(staged_csv, output_paths.config)

        completed_at = datetime.now(timezone.utc).replace(microsecond=0)
        price_fields = [
            field for field in config.update.fields if field in SUPPORTED_PRICE_FIELDS
        ]
        export_summary = export_dashboard(
            data_path=str(staged_csv),
            root_config_path=str(output_paths.config),
            output=str(staged_html),
            embedded_js_output=str(staged_embedded_js),
            compact_parquet_output=str(staged_parquet),
            fields=price_fields,
            precision=PRECISION,
            max_output_mb=config.update.standalone_max_mb,
            include_analytics=any(
                field in config.update.fields for field in ("PX_VOLUME", "VOL_30D")
            ),
            built_at=completed_at.isoformat().replace("+00:00", "Z"),
        )
        _verify_parquet_parity(combined, staged_parquet, price_fields)
        _write_csv_gzip(staged_csv, staged_csv_gzip)

        artifact_pairs = (
            (staged_csv, output_paths.csv),
            (staged_csv_gzip, output_paths.csv_gzip),
            (staged_parquet, output_paths.parquet),
            (staged_embedded_js, output_paths.embedded_js),
            (staged_html, output_paths.html),
        )
        artifact_manifest = {
            _relative_display(target, output_paths.project_root): {
                "bytes": staged.stat().st_size,
                "sha256": _sha256(staged),
            }
            for staged, target in artifact_pairs
        }
        manifest = {
            "schema_version": 1,
            "status": "complete",
            "source": "Bloomberg Desktop API",
            "requested_at": requested_at.isoformat().replace("+00:00", "Z"),
            "completed_at": completed_at.isoformat().replace("+00:00", "Z"),
            "mode": "full" if full else "incremental",
            "precision": PRECISION,
            "config": _relative_display(output_paths.config, output_paths.project_root),
            "config_sha256": _sha256(output_paths.config),
            "git_revision": _git_revision(),
            "requested_fields": list(config.update.fields),
            "requested_securities": len(requests),
            "universe_securities": len(specs),
            "received_rows": pulled.height,
            "retained_rows": combined.height,
            "data_min_date": combined["date"].min().isoformat(),
            "data_max_date": combined["date"].max().isoformat(),
            "root_coverage": _root_stats(combined),
            "warnings": list(dict.fromkeys(str(item) for item in pull_warnings)),
            "export": {
                "rows": export_summary["rows"],
                "roots": export_summary["roots"],
                "fields": export_summary["fields"],
                "standalone_mb": export_summary["output_mb"],
            },
            "artifacts": artifact_manifest,
        }
        staged_manifest.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        _promote_artifacts(
            (*artifact_pairs, (staged_manifest, output_paths.manifest)),
            staging / "backups",
        )

    return {
        "ok": True,
        "success": True,
        "message": (
            f"Bloomberg update complete: {combined.height:,} rows through "
            f"{combined['date'].max().isoformat()}. Reloading…"
        ),
        "manifest": _relative_display(output_paths.manifest, output_paths.project_root),
        "rows": combined.height,
        "data_max_date": combined["date"].max().isoformat(),
        "warnings": list(dict.fromkeys(str(item) for item in pull_warnings)),
    }
