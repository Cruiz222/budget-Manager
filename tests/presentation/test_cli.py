import re
from uuid import uuid4

import pytest

from app.presentation.cli import main


def run(db_path, *argv):
    """Invoke the CLI in-process against the given database file."""
    return main(["--db", db_path, *argv])


def opened_wallet_id(db_path, capsys):
    assert run(db_path, "open", "--currency", "NGN") == 0
    out = capsys.readouterr().out
    match = re.search(r"opened wallet (\S+)", out)
    assert match, out
    return match.group(1)


def test_open_then_balance_round_trip(tmp_path, capsys):
    db = str(tmp_path / "cli.db")
    wallet_id = opened_wallet_id(db, capsys)

    assert run(db, "balance", wallet_id) == 0
    out = capsys.readouterr().out
    assert "status: active" in out
    assert "available: 0.00 NGN" in out
    assert "locked: 0.00 NGN" in out


def test_deposit_then_balance_shows_new_available(tmp_path, capsys):
    db = str(tmp_path / "cli.db")
    wallet_id = opened_wallet_id(db, capsys)

    assert run(db, "deposit", wallet_id, "2500.50") == 0
    out = capsys.readouterr().out
    assert "deposited 2500.50 NGN" in out

    assert run(db, "balance", wallet_id) == 0
    out = capsys.readouterr().out
    assert "available: 2500.50 NGN" in out


def test_withdraw_beyond_balance_fails_and_balance_is_unchanged(tmp_path, capsys):
    db = str(tmp_path / "cli.db")
    wallet_id = opened_wallet_id(db, capsys)
    run(db, "deposit", wallet_id, "1000")
    capsys.readouterr()

    assert run(db, "withdraw", wallet_id, "5000") == 1
    assert "error:" in capsys.readouterr().err

    assert run(db, "balance", wallet_id) == 0
    assert "available: 1000.00 NGN" in capsys.readouterr().out


def test_operation_on_unknown_wallet_is_an_error(tmp_path, capsys):
    db = str(tmp_path / "cli.db")

    assert run(db, "deposit", str(uuid4()), "500") == 1
    assert "error:" in capsys.readouterr().err


def test_invalid_amount_is_a_usage_error(tmp_path):
    db = str(tmp_path / "cli.db")

    with pytest.raises(SystemExit) as excinfo:
        run(db, "deposit", str(uuid4()), "abc")
    assert excinfo.value.code == 2


def test_freeze_then_unfreeze_round_trip(tmp_path, capsys):
    db = str(tmp_path / "cli.db")
    wallet_id = opened_wallet_id(db, capsys)

    assert run(db, "freeze", wallet_id) == 0
    assert "is now frozen" in capsys.readouterr().out

    assert run(db, "balance", wallet_id) == 0
    out = capsys.readouterr().out
    assert "status: frozen" in out
    assert "available: 0.00 NGN" in out

    assert run(db, "unfreeze", wallet_id) == 0
    assert "is now active" in capsys.readouterr().out

    assert run(db, "balance", wallet_id) == 0
    assert "status: active" in capsys.readouterr().out


def test_freeze_stops_withdrawals_but_not_deposits(tmp_path, capsys):
    db = str(tmp_path / "cli.db")
    wallet_id = opened_wallet_id(db, capsys)
    run(db, "deposit", wallet_id, "1000")
    capsys.readouterr()
    run(db, "freeze", wallet_id)
    capsys.readouterr()

    # A frozen wallet rejects the withdrawal ...
    assert run(db, "withdraw", wallet_id, "300") == 1
    assert "error:" in capsys.readouterr().err

    # ... but the freeze was a status change only - no money moved.
    assert run(db, "balance", wallet_id) == 0
    assert "available: 1000.00 NGN" in capsys.readouterr().out

    # Top-ups are still allowed while frozen.
    assert run(db, "deposit", wallet_id, "500") == 0
    assert run(db, "balance", wallet_id) == 0
    assert "available: 1500.00 NGN" in capsys.readouterr().out


def test_history_lists_the_wallets_transactions_oldest_first(tmp_path, capsys):
    db = str(tmp_path / "cli.db")
    wallet_id = opened_wallet_id(db, capsys)

    run(db, "deposit", wallet_id, "5000")
    capsys.readouterr()
    run(db, "deposit", wallet_id, "2000.50")
    capsys.readouterr()

    assert run(db, "history", wallet_id) == 0
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.strip()]
    assert len(lines) == 2
    assert "deposit" in lines[0]
    assert "5000.00 NGN" in lines[0]
    assert "successful" in lines[0]
    assert "deposit" in lines[1]
    assert "2000.50 NGN" in lines[1]


def test_history_of_wallet_with_no_transactions_is_friendly(tmp_path, capsys):
    db = str(tmp_path / "cli.db")
    wallet_id = opened_wallet_id(db, capsys)

    assert run(db, "history", wallet_id) == 0
    out = capsys.readouterr().out
    assert "no transactions" in out


def test_history_of_unknown_wallet_is_an_error(tmp_path, capsys):
    db = str(tmp_path / "cli.db")

    assert run(db, "history", str(uuid4())) == 1
    assert "error:" in capsys.readouterr().err


def test_payout_sends_locked_funds_to_a_bank_account(tmp_path, capsys):
    db = str(tmp_path / "cli.db")
    wallet_id = opened_wallet_id(db, capsys)
    run(db, "deposit", wallet_id, "10000")
    capsys.readouterr()
    run(db, "lock", wallet_id, "6000")
    capsys.readouterr()

    assert run(
        db,
        "payout",
        wallet_id,
        "2000",
        "--account",
        "0123456789",
        "--bank-code",
        "058",
        "--name",
        "Chinedu Okafor",
    ) == 0
    out = capsys.readouterr().out
    assert "paid 2000.00 NGN to Chinedu Okafor (bank_account:0123456789)" in out
    assert "available 4000.00 NGN" in out
    assert "locked 4000.00 NGN" in out


def test_payout_beyond_the_locked_balance_is_an_error(tmp_path, capsys):
    db = str(tmp_path / "cli.db")
    wallet_id = opened_wallet_id(db, capsys)
    run(db, "deposit", wallet_id, "10000")
    capsys.readouterr()

    # Nothing is locked, so available balance cannot fund a payout.
    assert run(
        db,
        "payout",
        wallet_id,
        "2000",
        "--account",
        "0123456789",
        "--bank-code",
        "058",
        "--name",
        "Chinedu Okafor",
    ) == 1
    assert "error:" in capsys.readouterr().err

    assert run(db, "balance", wallet_id) == 0
    assert "available: 10000.00 NGN" in capsys.readouterr().out


def test_payout_appears_in_history(tmp_path, capsys):
    db = str(tmp_path / "cli.db")
    wallet_id = opened_wallet_id(db, capsys)
    run(db, "deposit", wallet_id, "10000")
    capsys.readouterr()
    run(db, "lock", wallet_id, "6000")
    capsys.readouterr()
    run(
        db,
        "payout",
        wallet_id,
        "2000",
        "--account",
        "0123456789",
        "--bank-code",
        "058",
        "--name",
        "Chinedu Okafor",
    )
    capsys.readouterr()

    assert run(db, "history", wallet_id) == 0
    out = capsys.readouterr().out
    assert "payout" in out
    assert "2000.00 NGN" in out


def test_payout_without_a_destination_is_a_usage_error(tmp_path):
    db = str(tmp_path / "cli.db")

    with pytest.raises(SystemExit) as excinfo:
        run(db, "payout", str(uuid4()), "2000", "--account", "0123456789")
    assert excinfo.value.code == 2


def test_unknown_command_is_a_usage_error(tmp_path):
    db = str(tmp_path / "cli.db")

    with pytest.raises(SystemExit) as excinfo:
        run(db, "bogus")
    assert excinfo.value.code == 2
