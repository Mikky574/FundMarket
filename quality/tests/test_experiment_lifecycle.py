from decimal import Decimal

from src.qq_control import ledger_control
from src.qq_control.paper_ledger import PaperLedger


def test_archive_and_initialize_preserves_old_experiment(tmp_path, monkeypatch):
    active_state = tmp_path / "data" / "public_ledger" / "state.json"
    archive_root = tmp_path / "data" / "public_ledger_archive"
    monkeypatch.setattr(ledger_control, "STATE_PATH", active_state)
    monkeypatch.setattr(ledger_control, "ARCHIVE_ROOT", archive_root)

    old = PaperLedger(active_state)
    old.initialize("2026-07-01", "2026-08-01", Decimal("100000"))

    result = ledger_control.archive_and_initialize_experiment({
        "experiment_name": "重新开始测试",
        "start_date": "2026-09-01",
        "end_date": "2026-12-31",
        "initial_cash": "50000",
        "user_confirmation": "确认归档旧实验并创建新实验",
    })

    archived = archive_root / result["archived_experiment_id"]
    assert (archived / "state.json").exists()
    assert (archived / "archive_manifest.json").exists()
    assert result["experiment"]["name"] == "重新开始测试"

    active = PaperLedger(active_state).state
    assert active["initial_cash"] == "50000.00"
    assert active["experiment"]["experiment_id"] == result["experiment"]["experiment_id"]
    assert active["positions"] == {}
