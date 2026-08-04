# Pricing Dashboard — Trade Builder

This repository is the standalone trade-builder portion of `Pricing_Dashboard`. Basis, Margins, Options/Liquidity, and Colonial are intentionally excluded.

The owner dashboard can now update itself from Bloomberg Desktop API. Pressing **UPDATE DATA** runs one fixed local pipeline:

```text
security_roots.xlsx
        ↓
Bloomberg Desktop API at localhost:8194
        ↓
rounded canonical CSV
        ↓
compressed CSV + Zstandard Parquet + embedded browser data
        ↓
portable standalone HTML + verification manifest
```

The update action exists only in the local owner server. The exported HTML remains offline, contains no Bloomberg credentials or API code, and does all trade math and unit conversion in browser JavaScript.

## Owner setup on the Bloomberg workstation

Bloomberg Terminal must be open and logged in on the licensed Windows computer.

```bat
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-bloomberg.txt
UPDATE_AND_OPEN.bat
```

Then press **UPDATE DATA** in the dashboard. The button stays hidden in a downloaded `file://` export and appears only when the local owner server is running.

The macOS launcher is `START_PRICING_DASHBOARD.command`. It is useful for dashboard development, but a normal Bloomberg Professional Desktop API session is expected to run on the licensed Bloomberg Windows workstation.

For a manual update:

```bash
python3 scripts/update_from_bloomberg.py
```

Use `--full` to discard the incremental cache and re-pull the complete configured history. Normal updates re-pull only active contracts from their last stored date minus the configured overlap, while retaining completed history.

## Shareable export package

A successful update publishes all files from the same validated CSV snapshot:

- `data/pricing_history.csv` — full, plain owner-side Bloomberg history; intentionally ignored by Git because it can be large.
- `dist/pricing_data.csv.gz` — full five-decimal CSV compressed for sharing and spreadsheet/BI use.
- `dist/pricing_data.parquet` — memory-efficient Zstandard Parquet for analytical use.
- `dist/pricing_dashboard_trade_builder.html` — portable, self-contained dashboard.
- `dist/update_manifest.json` — row counts, per-root coverage and freshness, fields, hashes, sizes, warnings, and lineage.
- `app/static/embedded_data.js` — compressed local-browser snapshot.

Recipients can open the HTML and use the dashboard without Bloomberg, Python, a server, or internet access. They can also decompress `pricing_data.csv.gz` or read the companion Parquet directly. Confirm that the intended recipient group is permitted to receive Bloomberg-derived data under your firm’s Bloomberg entitlements and redistribution terms.

Publication is transactional. Bloomberg connection failures, request timeouts, invalid roots, incomplete enabled-root coverage, schema failures, Parquet/CSV mismatches, or an oversized HTML export leave the prior successful package untouched.

## Trade Builder workspaces

The dashboard has three separate workspaces:

1. **Spreads** — Single, Spread, Fly, and Box structures.
2. **Prebuilt** — named structures with a simple month selector.
3. **Multi-Leg (Custom)** — up to eight independently weighted legs.

All per-leg unit conversion occurs before ratios are applied, so mixed-unit trades are valid. The four supported price sources are `PX_LAST`, `PX_CLOSE`, `PX_SETTLE`, and `PX_FAIR_1430` when Bloomberg returns them.

## Add or change a security root

Open [`config/security_roots.xlsx`](config/security_roots.xlsx) and edit the **Security Roots** sheet. One row controls one Bloomberg root without a Python or JavaScript edit.

| Field | Purpose | Example |
|---|---|---|
| `enabled` | Include the root in Bloomberg and the export | `TRUE` |
| `root` | Exact Bloomberg root, normalized to uppercase | `WU`, `HO` |
| `display_name` | Dashboard label | `GC Jet`, `Heating Oil` |
| `yellow_key` | Bloomberg security type | `Comdty` or `Index` |
| `native_unit` | Native Bloomberg quote unit | `cpg`, `$/gal`, `$/bbl`, `$/MT` |
| `bbl_per_mt` | Product density conversion | `7.45` |
| `gal_per_bbl` | Gallons per barrel | `42` |
| `ticker_template` | Contract construction rule | `{root}{month_code}{yy} {yellow_key}` |
| `tradingview_symbol` | Optional external symbol | `NYMEX:HO` |
| `aliases` | Old roots/names separated by `|` | `ME|GC JET` |
| `product_group` | Sidebar grouping | `Refined Products` |
| `sort_order` | Display order | `10` |

The workbook already includes `WU` for GC Jet and `HO` for Heating Oil. Yellow key and unit fields use dropdowns. A review-friendly root mirror is available at [`config/security_roots.example.csv`](config/security_roots.example.csv).

The **Bloomberg Update** sheet controls:

- earliest retained history date;
- first and last delivery years;
- history months requested before each delivery;
- maximum dated-contract reference depth;
- incremental overlap days;
- requested Bloomberg fields;
- Bloomberg host, port, and service;
- batch size and per-request timeout;
- maximum standalone HTML size.

For example, the standard template generates `WUF26 Comdty` for January 2026 GC Jet. Changing a root to `Index` makes the same rule end in `Index` automatically.

## Unit conversion contract

Each leg is converted from its configured native unit into the selected dashboard unit before ratios are applied:

```text
cpg -> $/gal = value / 100
$/gal -> $/bbl = value * gal_per_bbl
$/bbl -> $/MT = value * bbl_per_mt
```

The same path is reversed for target-unit conversion. Spreadsheet formula strings are never evaluated.

## Precision and memory behavior

- CSV, Parquet, embedded data, and calculated downloads are capped at five decimal places.
- Parquet uses Zstandard compression and bounded row groups.
- The full handoff CSV is gzip-compressed instead of duplicated raw in the HTML.
- The portable HTML contains one gzip-compressed data payload and vendors Plotly, theme code, and trade math.
- `built_at` and `data_max_date` remain separate, so rebuilding cannot make stale prices appear current.
- The default 20 MB standalone-file budget fails visibly if a data expansion becomes too large.

## Build without Bloomberg

The committed sample is deterministic and exercises CL, CO, HO, RB, QS, and WU in their native units.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/validate_pricing_data.py data/sample_market_data.parquet
.venv/bin/python scripts/build_dashboard.py
open dist/pricing_dashboard_trade_builder.html
```

`scripts/build_dashboard.py` accepts CSV or Parquet and writes the standalone HTML, compact Parquet, and local embedded snapshot. The Bloomberg updater is the path that additionally writes the canonical CSV, compressed CSV, and manifest.

## Verification

```bash
python3 -m unittest discover -s tests -p "test_*.py"
npm test
npm run check
python3 scripts/validate_pricing_data.py data/sample_market_data.parquet
python3 scripts/build_dashboard.py
```

The automated suite covers spreadsheet ticker expansion, Comdty/Index suffixes, Bloomberg partial/final events and failure cleanup, dated references, incremental merge behavior, five-decimal artifacts, CSV/Parquet parity, single-flight update requests, offline boundaries, and rollback after a late export failure.
