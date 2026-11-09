import sys
import os
import shutil
from pathlib import Path
import pytest

# Ensure project root is in sys.path
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)
# Also make test helpers (e.g. tests/mock_driver.py) importable as
# top-level modules, as pytest's prepend mode already does for test files.
_tests_dir = os.path.dirname(os.path.abspath(__file__))
if _tests_dir not in sys.path:
    sys.path.insert(0, _tests_dir)


_LLM_ENV_VARS = ("LMSTUDIO_BASE_URL", "LLM_API_KEY", "LLM_PROVIDER", "LLM_MODEL")


@pytest.fixture(scope="session", autouse=True)
def _snapshot_llm_env():
    """Snapshot LLM-related env vars at session start (for isolation)."""
    yield {name: os.environ.get(name) for name in _LLM_ENV_VARS}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Isolate each test from local ``.env`` LLM values.

    In particular, ``LMSTUDIO_BASE_URL`` is reset to the canonical
    ``http://localhost:1234/v1`` for every test (tests that need a
    different value can still ``monkeypatch.setenv`` it themselves,
    which runs after this fixture), and the original value is restored
    after the test.
    """
    monkeypatch.setenv("LMSTUDIO_BASE_URL", "http://localhost:1234/v1")
    yield


@pytest.fixture
def isolated_database(tmp_path, monkeypatch):
    """
    Database isolation fixture (Feature 8).
    Copies data/transactions.csv and data/customer_accounts.csv to a temporary directory
    and monkeypatches app.database constants (DATABASE_FILE, CUSTOMER_ACCOUNTS_FILE, DATA_DIR)
    so tests execute against an isolated sandbox without polluting production CSV data.
    """
    import app.database

    data_dir = Path(root_dir) / "data"
    src_tx = data_dir / "transactions.csv"
    src_acc = data_dir / "customer_accounts.csv"

    tmp_tx = tmp_path / "transactions.csv"
    tmp_acc = tmp_path / "customer_accounts.csv"

    if src_tx.exists():
        shutil.copyfile(src_tx, tmp_tx)
    if src_acc.exists():
        shutil.copyfile(src_acc, tmp_acc)

    monkeypatch.setattr(app.database, "DATABASE_FILE", tmp_tx)
    monkeypatch.setattr(app.database, "CUSTOMER_ACCOUNTS_FILE", tmp_acc)
    monkeypatch.setattr(app.database, "DATA_DIR", tmp_path)

    yield {
        "transactions_file": tmp_tx,
        "accounts_file": tmp_acc,
        "dir": tmp_path,
    }
