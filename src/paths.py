"""Canonical repository paths shared by runtime modules and maintenance tools."""
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = SOURCE_ROOT.parent
DATA_ROOT = PROJECT_ROOT / "data"

# Public-paper experiments are stored separately from source code.  Code must
# use these paths rather than constructing the retired ``paper/`` location.
PUBLIC_LEDGER_ROOT = DATA_ROOT / "public_ledger"
PUBLIC_LEDGER_STATE_PATH = PUBLIC_LEDGER_ROOT / "state.json"
PUBLIC_LEDGER_BENCHMARK_PATH = PUBLIC_LEDGER_ROOT / "benchmarks.json"
PUBLIC_LEDGER_FEE_ROOT = PUBLIC_LEDGER_ROOT / "fees"
PUBLIC_LEDGER_DATA_ROOT = PUBLIC_LEDGER_ROOT / "data"
PUBLIC_LEDGER_REPORTS_ROOT = PUBLIC_LEDGER_ROOT / "reports"
PUBLIC_FUND_POOL_PATH = PUBLIC_LEDGER_ROOT / "fund_pool.json"

STATIC_ROOT = SOURCE_ROOT / "web_user" / "static"
