#!/bin/zsh
set -u

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

if [[ -x ".venv/bin/python" ]]; then
    exec ".venv/bin/python" scripts/run_dashboard.py --open
fi

if command -v python3 >/dev/null 2>&1; then
    exec python3 scripts/run_dashboard.py --open
fi

if command -v python >/dev/null 2>&1; then
    exec python scripts/run_dashboard.py --open
fi

if command -v py >/dev/null 2>&1; then
    exec py -3 scripts/run_dashboard.py --open
fi

echo "Python was not found. Install Python 3, then run this launcher again."
read -r "?Press Return to close..."
exit 1
