# Pricing Dashboard — Trade Builder

This repository is the standalone trade-builder portion of `Pricing_Dashboard`. It contains only the production pricing surface; Basis, Margins, Options/Liquidity, and Colonial are intentionally excluded.

The dashboard has three separate workspaces:

1. **Spreads** — Single, Spread, Fly, and Box structures.
2. **Prebuilt** — named structures with a simple month selector.
3. **Multi-Leg (Custom)** — up to eight independently weighted legs.

All trade arithmetic, per-leg unit conversion, VaR calculations, and chart updates run in browser JavaScript. The final HTML does not need Python, FastAPI, or a database.

## Fast start

### macOS/Linux

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/validate_pricing_data.py data/sample_market_data.parquet
.venv/bin/python scripts/build_dashboard.py
open dist/pricing_dashboard_trade_builder.html
```

### Windows

```bat
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe scripts\validate_pricing_data.py data\sample_market_data.parquet
.venv\Scripts\python.exe scripts\build_dashboard.py
start "" dist\pricing_dashboard_trade_builder.html
```

The build produces:

- `dist/pricing_dashboard_trade_builder.html` — one portable, self-contained dashboard.
- `dist/pricing_data.parquet` — rounded, Zstandard-compressed audit/handoff data.
- `app/static/embedded_data.js` — compressed data for local template development.

The latest verified standalone HTML and companion Parquet are committed in `dist/`, so a user can download and open the dashboard without running a server. Rebuild both after changing data or the security-root workbook.

## Add or change a security root

Open [`config/security_roots.xlsx`](config/security_roots.xlsx) and edit the **Security Roots** sheet. Each row controls one Bloomberg root without a Python or JavaScript edit.

Required fields:

| Field | Purpose | Example |
|---|---|---|
| `enabled` | Include the root in the build | `TRUE` |
| `root` | Exact Bloomberg root, normalized to uppercase | `WU`, `HO` |
| `display_name` | Dashboard label | `GC Jet`, `Heating Oil` |
| `yellow_key` | Bloomberg security type | `Comdty` or `Index` |
| `native_unit` | Unit of the source prices | `cpg`, `$/gal`, `$/bbl`, `$/MT` |
| `bbl_per_mt` | Product density conversion | `7.45` |
| `gal_per_bbl` | Gallons per barrel | `42` |
| `ticker_template` | Contract construction rule | `{root}{month_code}{yy} {yellow_key}` |
| `tradingview_symbol` | Optional external symbol | `NYMEX:HO` |
| `aliases` | Optional old roots/names separated by `|` | `ME|GC JET` |
| `product_group` | Right-sidebar grouping metadata | `Refined Products` |
| `sort_order` | Display order | `10` |

The workbook already includes `WU` for GC Jet and `HO` for Heating Oil. Dropdowns constrain yellow keys and units. A review-friendly mirror is available at [`config/security_roots.example.csv`](config/security_roots.example.csv).

After editing the workbook:

```bash
python3 scripts/validate_pricing_data.py /path/to/pricing.parquet \
  --config config/security_roots.xlsx

python3 scripts/build_dashboard.py \
  --data /path/to/pricing.parquet \
  --config config/security_roots.xlsx
```

The validator stops on duplicate roots, invalid units or yellow keys, missing conversion factors, mismatched Bloomberg suffixes, unconfigured data roots, inconsistent root factors, duplicate price keys, or values with more than five decimal places.

## Pricing data contract

Parquet is the canonical input. CSV and IPC are accepted for transition work, but the build always emits compact Parquet.

Required columns:

- `date`
- `security_str` or `FUT_CUR_GEN_TICKER`
- `security_prefix` or another configured root column
- `PX_LAST`

Recommended columns:

- `PX_CLOSE`, `PX_SETTLE`, `PX_FAIR_1430`
- `CLEAN_NAME`
- `month`, `contract_month_yr`, `contract_year`
- `frequency`, `reference`
- `VOL_30D`, `PX_VOLUME`
- `bbl_per_mt`, `gal_per_bbl`

The root must match the spreadsheet exactly. Contract strings such as `WUF26 Comdty` and `HOF26 Comdty` are supported.

## Unit conversion contract

Each leg is converted from its own configured native unit into the selected dashboard unit before ratios are applied. Mixed-unit trades are therefore valid.

The browser uses these relationships:

```text
cpg -> $/gal = value / 100
$/gal -> $/bbl = value * gal_per_bbl
$/bbl -> $/MT = value * bbl_per_mt
```

The same path is reversed for target-unit conversion. Spreadsheet formula strings are never evaluated.

## Precision and export behavior

- Every source/export float is capped at five decimal places.
- The compact companion Parquet uses Zstandard compression.
- The portable HTML contains one gzip-compressed payload; it does not duplicate raw JSON.
- `built_at` and `data_max_date` are separate, so rebuilding cannot make stale prices appear current.
- Plotly, theme code, trade math, and data are inlined in the portable HTML. The optional TradingView button is the only feature that intentionally opens an external service.

To keep only selected price fields:

```bash
python3 scripts/build_dashboard.py --fields PX_LAST,PX_SETTLE
```

Volume and precomputed 30-day volatility arrays are excluded by default to keep the portable file lean. Include them only when needed:

```bash
python3 scripts/build_dashboard.py --include-analytics
```

The default build also enforces a 20 MB standalone-file budget so an unexpected data expansion fails visibly instead of producing an oversized handoff.

## Verification

```bash
python3 -m unittest discover -s tests -p "test_*.py"
npm test
npm run check
python3 scripts/validate_pricing_data.py data/sample_market_data.parquet
python3 scripts/build_dashboard.py
```

No `npm install` is needed: JavaScript checks use Node's built-in test runner and the dashboard vendors its browser dependencies.

## Regenerate the sample

```bash
python3 scripts/generate_mock_market_data.py
```

The generated sample includes CL, CO, HO, RB, QS, and WU, with native quote units chosen to exercise all conversion paths.
