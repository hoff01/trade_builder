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
INSTALL_BLOOMBERG.bat
UPDATE_AND_OPEN.bat
```

`INSTALL_BLOOMBERG.bat` prefers 64-bit Python 3.13 and falls back to 3.12. It creates the reusable environment at `%USERPROFILE%\Pyenvs\trade_builder`, installs the full Polars/dashboard stack from PyPI, downloads `blpapi` only from Bloomberg's official package index, and runs native import checks. `UPDATE_AND_OPEN.bat` runs the same self-healing bootstrap automatically before opening the dashboard. It invokes the managed environment's Python directly, so it never falls back to an unconfigured system Python.

For a manual install, use the same interpreter for every command:

```bat
py -3.13 -m venv "%USERPROFILE%\Pyenvs\trade_builder"
"%USERPROFILE%\Pyenvs\trade_builder\Scripts\python.exe" -m pip install -r requirements.txt
"%USERPROFILE%\Pyenvs\trade_builder\Scripts\python.exe" -m pip install --index-url=https://blpapi.bloomberg.com/repository/releases/python/simple/ blpapi
"%USERPROFILE%\Pyenvs\trade_builder\Scripts\python.exe" scripts\check_runtime_compatibility.py
```

Replace `-3.13` with `-3.12` when needed. `https://bloomberg.com` is Bloomberg's website, not a Python package index; pip must use the dedicated `blpapi.bloomberg.com/repository/releases/python/simple` URL above.

Then press **UPDATE DATA** in the dashboard. The button stays hidden in a downloaded `file://` export and appears only when the local owner server is running.

### Verified Python compatibility

The dependency ranges in `requirements.txt` and `requirements-bloomberg.txt` are supported on CPython 3.12 and 3.13. A clean compatibility check on August 4, 2026 resolved and imported:

| Python | Bloomberg BLPAPI | Polars | Result |
|---|---:|---:|---|
| 3.12.12 | 3.26.6.1 | 1.43.2 | Native import and smoke check passed |
| 3.13.12 | 3.26.6.1 | 1.43.2 | Native import and smoke check passed |

Bloomberg and Polars also publish `win_amd64` wheels compatible with both versions. This verifies package installation and native loading; a live Bloomberg data request still requires Bloomberg Terminal to be open, logged in, licensed, and reachable at `localhost:8194`.

The macOS launcher is `START_PRICING_DASHBOARD.command`. It is useful for dashboard development, but a normal Bloomberg Professional Desktop API session is expected to run on the licensed Bloomberg Windows workstation.

For a manual update:

```bash
python3 scripts/update_from_bloomberg.py
```

Use `--full` to discard the incremental cache and re-pull the complete configured history. Normal updates re-pull only active contracts from their last stored date minus the configured overlap, while retaining completed history.

## Complete the live dashboard

1. Edit every enabled row in `config/security_roots.xlsx`: verify `root`, `common_name`, `yellow_key`, `native_unit`, conversion factors, `ticker_template`, and `curve_mode` against Bloomberg FLDS/security lookup.
2. Review the **Bloomberg Update** sheet: dates, delivery-year range, history months, reference depth, full-data `fields`, and lightweight `dashboard_fields`.
3. On the licensed Windows workstation, open and log in to Bloomberg Terminal.
4. Run `INSTALL_BLOOMBERG.bat`, then run `UPDATE_AND_OPEN.bat`.
5. Run one full pull after changing ticker formats, yellow keys, curve modes, fields, history depth, or the start date:

   ```bat
   "%USERPROFILE%\Pyenvs\trade_builder\Scripts\python.exe" scripts\update_from_bloomberg.py --full
   ```

6. Open the local dashboard and test all three workspaces, every configured root, unit conversion, price field, VaR, and CSV export.
7. Check `dist/update_manifest.json`: status must be `complete`, every enabled root must appear under `root_coverage`, and warnings must be understood.
8. Copy `dist/pricing_dashboard_trade_builder.html`, `dist/pricing_data.csv.gz`, and `dist/pricing_data.parquet` to a machine without Bloomberg and verify the portable handoff.

After the first full pull, use **UPDATE DATA** for normal incremental refreshes.

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

All per-leg unit conversion occurs before ratios are applied, so mixed-unit trades are valid. The four supported price sources are `PX_LAST`, `PX_CLOSE`, `PX_SETTLE`, and `PX_FAIR_1430` when Bloomberg returns them and they are listed in `dashboard_fields`. The default portable dashboard embeds only `PX_LAST`; the companion CSV and Parquet retain every requested `fields` value.

## Add or change a security root

Open [`config/security_roots.xlsx`](config/security_roots.xlsx) and edit the **Security Roots** sheet. One row controls one Bloomberg root without a Python or JavaScript edit.

| Field | Purpose | Example |
|---|---|---|
| `enabled` | Include the root in Bloomberg and the export | `TRUE` |
| `root` | Exact Bloomberg root, normalized to uppercase | `WU`, `HO`, `RVO` |
| `common_name` | Authoritative label used everywhere in the dashboard and exports | `GC Jet`, `Heating Oil` |
| `yellow_key` | Bloomberg security type | `Comdty` or `Index` |
| `native_unit` | Native Bloomberg quote unit | `cpg`, `$/gal`, `$/bbl`, `$/MT` |
| `bbl_per_mt` | Product density conversion | `7.45` |
| `gal_per_bbl` | Gallons per barrel | `42` |
| `ticker_template` | Contract construction rule and year style | `{root}{month_code}{y} {yellow_key}` |
| `curve_mode` | `Monthly` builds dated contracts; `Flat` pulls one monthless daily series | `Monthly`, `Flat` |
| `tradingview_symbol` | Optional external symbol | `NYMEX:HO` |
| `aliases` | Old roots/names separated by `|` | `ME|GC JET` |
| `product_group` | Sidebar grouping | `Refined Products` |
| `sort_order` | Display order | `10` |

The workbook already includes `WU` for GC Jet, `HO` for Heating Oil, and `RVO` as the monthless `RVO Index`. Change `common_name` once and that label is mapped into the canonical CSV, Parquet, standalone export, sidebar, selectors, and chart metadata. Yellow key, unit, and curve mode fields use dropdowns. Existing files that still use `display_name` or have no `curve_mode` column remain supported; missing curve modes default to `Monthly`. A review-friendly root mirror is available at [`config/security_roots.example.csv`](config/security_roots.example.csv).

For `curve_mode=Flat`, the updater requests only the configured undated Bloomberg ticker. The trade leg has no month selector. JavaScript uses that date's observation unchanged for every point on the selected forward curve, so RVO does not acquire an artificial monthly shape.

The **Bloomberg Update** sheet controls:

- earliest retained history date;
- first and last delivery years;
- history months requested before each delivery;
- maximum dated-contract reference depth;
- incremental overlap days;
- `fields`, the Bloomberg fields retained in the full CSV and Parquet;
- `dashboard_fields`, the smaller subset embedded in the portable dashboard and shown in its price-source selector;
- Bloomberg host, port, and service;
- batch size and per-request timeout;
- maximum standalone HTML size.

The year placeholder and yellow key are independent:

- `{root}{month_code}{y} {yellow_key}` generates `HOG6 Comdty` for February 2026 Heating Oil.
- Replace `{y}` with `{yy}` to generate `HOG26 Comdty`.
- Replace `{y}` with `{year}` to generate `HOG2026 Comdty`.
- Change `yellow_key` from `Comdty` to `Index` to make the same ticker end in `Index`.
- `{root} {yellow_key}` with `curve_mode=Flat` generates one `RVO Index` request with no month or year suffix.

The standard workbook uses Bloomberg-style one-digit years. Each root can use its own template and yellow key.

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
- The portable HTML contains one gzip-compressed data payload and vendors Plotly's official basic bundle, theme code, and trade math.
- `dashboard_fields` can keep the portable browser payload lean without removing fields from the full CSV or Parquet.
- The local owner server revalidates cached static assets while keeping update API responses uncached.
- `built_at` and `data_max_date` remain separate, so rebuilding cannot make stale prices appear current.
- The default 20 MB standalone-file budget fails visibly if a data expansion becomes too large.

## Build without Bloomberg

The committed sample is deterministic and exercises CL, CO, HO, RB, QS, WU, and the monthless RVO flat curve in their native units.

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

On the Bloomberg workstation, also run `%USERPROFILE%\Pyenvs\trade_builder\Scripts\python.exe scripts\check_runtime_compatibility.py`. It validates the Python version and exercises the Bloomberg and Polars native bindings without opening a Bloomberg session.

The automated suite covers spreadsheet ticker expansion, Comdty/Index suffixes, Bloomberg partial/final events and failure cleanup, dated references, incremental merge behavior, five-decimal artifacts, CSV/Parquet parity, single-flight update requests, offline boundaries, and rollback after a late export failure.
