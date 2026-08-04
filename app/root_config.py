"""Validated spreadsheet configuration for Pricing Dashboard security roots."""

from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from openpyxl import load_workbook

SHEET_NAME = "Security Roots"
ROOT_COLUMNS = (
    "enabled",
    "root",
    "display_name",
    "yellow_key",
    "native_unit",
    "bbl_per_mt",
    "gal_per_bbl",
    "ticker_template",
    "tradingview_symbol",
    "aliases",
    "product_group",
    "sort_order",
)
VALID_YELLOW_KEYS = {"comdty": "Comdty", "index": "Index"}
VALID_UNITS = ("cpg", "$/gal", "$/bbl", "$/MT")
UNIT_ALIASES = {
    "cpg": "cpg",
    "centpergallon": "cpg",
    "centspergallon": "cpg",
    "cents/gal": "cpg",
    "$/gal": "$/gal",
    "usd/gal": "$/gal",
    "dollarspergallon": "$/gal",
    "dollarpergallon": "$/gal",
    "$/bbl": "$/bbl",
    "usd/bbl": "$/bbl",
    "dollarsperbarrel": "$/bbl",
    "dollarperbarrel": "$/bbl",
    "$/mt": "$/MT",
    "usd/mt": "$/MT",
    "dollarspermetricton": "$/MT",
    "dollarpermetricton": "$/MT",
}
TRUE_VALUES = {"1", "true", "yes", "y", "on", "enabled"}
FALSE_VALUES = {"0", "false", "no", "n", "off", "disabled"}
ROOT_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9._-]*$")


class ConfigValidationError(ValueError):
    """Raised with all actionable spreadsheet problems at once."""

    def __init__(self, issues: Iterable[str]):
        self.issues = tuple(str(issue) for issue in issues)
        super().__init__("Security root configuration is invalid:\n- " + "\n- ".join(self.issues))


@dataclass(frozen=True)
class SecurityRoot:
    enabled: bool
    root: str
    display_name: str
    yellow_key: str
    native_unit: str
    bbl_per_mt: float
    gal_per_bbl: float
    ticker_template: str
    tradingview_symbol: str
    aliases: tuple[str, ...]
    product_group: str
    sort_order: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "root": self.root,
            "display_name": self.display_name,
            "yellow_key": self.yellow_key,
            "native_unit": self.native_unit,
            "bbl_per_mt": self.bbl_per_mt,
            "gal_per_bbl": self.gal_per_bbl,
            "ticker_template": self.ticker_template,
            "tradingview_symbol": self.tradingview_symbol,
            "aliases": list(self.aliases),
            "product_group": self.product_group,
            "sort_order": self.sort_order,
        }


@dataclass(frozen=True)
class RootConfig:
    roots: tuple[SecurityRoot, ...]
    source_path: Path

    @property
    def enabled_roots(self) -> tuple[SecurityRoot, ...]:
        return tuple(root for root in self.roots if root.enabled)

    @property
    def enabled_root_codes(self) -> tuple[str, ...]:
        return tuple(root.root for root in self.enabled_roots)

    @property
    def by_root(self) -> dict[str, SecurityRoot]:
        return {root.root: root for root in self.roots}

    @property
    def alias_to_root(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for item in self.enabled_roots:
            result[item.root.upper()] = item.root
            for alias in item.aliases:
                result[alias.upper()] = item.root
        return result

    def resolve_root(self, value: object) -> str | None:
        key = str(value or "").strip().upper()
        return self.alias_to_root.get(key)

    def to_dict(self) -> dict[str, dict[str, Any]]:
        return {
            item.root: item.to_dict()
            for item in sorted(self.enabled_roots, key=lambda root: (root.sort_order, root.root))
        }


def normalize_unit(value: object) -> str:
    text = str(value or "").strip().lower().replace(" ", "").replace("_", "")
    return UNIT_ALIASES.get(text, "")


def _parse_enabled(value: object, row_number: int, issues: list[str]) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return True
    if text in TRUE_VALUES:
        return True
    if text in FALSE_VALUES:
        return False
    issues.append(f"row {row_number}: enabled must be TRUE or FALSE, got {value!r}")
    return False


def _positive_float(value: object, field: str, row_number: int, issues: list[str]) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        issues.append(f"row {row_number}: {field} must be a positive number")
        return 0.0
    if not math.isfinite(number) or number <= 0:
        issues.append(f"row {row_number}: {field} must be a positive finite number")
        return 0.0
    return round(number, 5)


def _sort_order(value: object, row_number: int, issues: list[str]) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        issues.append(f"row {row_number}: sort_order must be an integer")
        return row_number
    if number < 0:
        issues.append(f"row {row_number}: sort_order must be zero or greater")
    return number


def _split_aliases(value: object) -> tuple[str, ...]:
    text = str(value or "").strip()
    if not text:
        return ()
    parts = re.split(r"[|,;]", text)
    return tuple(dict.fromkeys(part.strip().upper() for part in parts if part.strip()))


def _read_csv_rows(path: Path) -> list[tuple[int, dict[str, Any]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = tuple(reader.fieldnames or ())
        missing = [column for column in ROOT_COLUMNS if column not in headers]
        if missing:
            raise ConfigValidationError([f"missing spreadsheet columns: {', '.join(missing)}"])
        return [(index, dict(row)) for index, row in enumerate(reader, start=2)]


def _read_xlsx_rows(path: Path) -> list[tuple[int, dict[str, Any]]]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        if SHEET_NAME not in workbook.sheetnames:
            raise ConfigValidationError([f"workbook must contain a '{SHEET_NAME}' sheet"])
        sheet = workbook[SHEET_NAME]
        values = sheet.iter_rows(values_only=True)
        headers = tuple(str(value or "").strip() for value in next(values, ()))
        missing = [column for column in ROOT_COLUMNS if column not in headers]
        if missing:
            raise ConfigValidationError([f"missing spreadsheet columns: {', '.join(missing)}"])
        rows: list[tuple[int, dict[str, Any]]] = []
        for row_number, values_row in enumerate(values, start=2):
            row = dict(zip(headers, values_row))
            if any(value not in (None, "") for value in row.values()):
                rows.append((row_number, row))
        return rows
    finally:
        workbook.close()


def load_root_config(path: str | Path) -> RootConfig:
    source = Path(path).expanduser().resolve()
    if not source.exists():
        raise ConfigValidationError([f"configuration file not found: {source}"])
    suffix = source.suffix.lower()
    if suffix == ".csv":
        raw_rows = _read_csv_rows(source)
    elif suffix in {".xlsx", ".xlsm"}:
        raw_rows = _read_xlsx_rows(source)
    else:
        raise ConfigValidationError(["configuration must be an .xlsx, .xlsm, or .csv file"])

    issues: list[str] = []
    roots: list[SecurityRoot] = []
    seen_roots: dict[str, int] = {}
    alias_owner: dict[str, str] = {}

    for row_number, row in raw_rows:
        enabled = _parse_enabled(row.get("enabled"), row_number, issues)
        root = str(row.get("root") or "").strip().upper()
        display_name = str(row.get("display_name") or "").strip()
        yellow_raw = str(row.get("yellow_key") or "").strip().lower()
        yellow_key = VALID_YELLOW_KEYS.get(yellow_raw, "")
        native_unit = normalize_unit(row.get("native_unit"))
        bbl_per_mt = _positive_float(row.get("bbl_per_mt"), "bbl_per_mt", row_number, issues)
        gal_per_bbl = _positive_float(row.get("gal_per_bbl"), "gal_per_bbl", row_number, issues)
        ticker_template = str(row.get("ticker_template") or "").strip()
        aliases = _split_aliases(row.get("aliases"))
        product_group = str(row.get("product_group") or "Other").strip() or "Other"

        if not root:
            issues.append(f"row {row_number}: root is required")
        elif not ROOT_PATTERN.fullmatch(root):
            issues.append(f"row {row_number}: root {root!r} contains unsupported characters")
        elif root in seen_roots:
            issues.append(f"row {row_number}: duplicate root {root!r} (first seen on row {seen_roots[root]})")
        else:
            seen_roots[root] = row_number
        if not display_name:
            issues.append(f"row {row_number}: display_name is required")
        if not yellow_key:
            issues.append(f"row {row_number}: yellow_key must be Comdty or Index")
        if not native_unit:
            issues.append(
                f"row {row_number}: native_unit must be one of {', '.join(VALID_UNITS)}"
            )
        required_tokens = ("{root}", "{month_code}", "{yellow_key}")
        missing_tokens = [token for token in required_tokens if token not in ticker_template]
        if not any(token in ticker_template for token in ("{yy}", "{year_2d}", "{year}")):
            missing_tokens.append("{yy} (or {year_2d}/{year})")
        if missing_tokens:
            issues.append(
                f"row {row_number}: ticker_template is missing {', '.join(missing_tokens)}"
            )
        for alias in aliases:
            if alias == root:
                continue
            previous = alias_owner.get(alias)
            if previous and previous != root:
                issues.append(f"row {row_number}: alias {alias!r} is already assigned to {previous}")
            if alias in seen_roots and alias != root:
                issues.append(f"row {row_number}: alias {alias!r} collides with a root")
            alias_owner[alias] = root

        roots.append(
            SecurityRoot(
                enabled=enabled,
                root=root,
                display_name=display_name,
                yellow_key=yellow_key,
                native_unit=native_unit,
                bbl_per_mt=bbl_per_mt,
                gal_per_bbl=gal_per_bbl,
                ticker_template=ticker_template,
                tradingview_symbol=str(row.get("tradingview_symbol") or "").strip(),
                aliases=aliases,
                product_group=product_group,
                sort_order=_sort_order(row.get("sort_order"), row_number, issues),
            )
        )

    if not roots:
        issues.append("configuration has no security-root rows")
    if roots and not any(root.enabled for root in roots):
        issues.append("configuration has no enabled security roots")
    if issues:
        raise ConfigValidationError(issues)
    return RootConfig(tuple(roots), source)
