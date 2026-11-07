import sys
import os
import shutil
from pathlib import Path
import pytest

# Ensure project root is in sys.path
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)


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
