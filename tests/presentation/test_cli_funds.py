"""The ``fund`` commands, exercised the way a user reaches them.

Same shape as ``test_cli.py`` and ``test_cli_plans.py``: everything goes through
``main(argv)`` against a real SQLite file, so a test covers argument parsing, the
service, the aggregate and the store in one pass.

**The dates here are relative to today, unlike almost everywhere else in this
suite.** Every other test pins a fixed moment, because every other test *passes*
the moment in. The ``fund`` commands are the ones place the moment is read from
the clock instead - ``datetime.now()`` at the edge of ``_fund_money`` and
``_fund_extend`` - so a hard-coded date would be a test that quietly changes
meaning as the years go by. A pot is either sealed or open *relative to now*, and
that is what ``days_from_now`` says.
"""

import re
from datetime import date, timedelta
from uuid import uuid4

import pytest

from app.presentation.cli import main


def run(db_path, *argv):
    """Invoke the CLI in-process against the given database file."""
    return main(["--db", db_path, *argv])


def days_from_now(days: int) -> str:
    """An ISO date ``days`` away from today, as the command line takes it."""
    return (date.today() + timedelta(days=days)).isoformat()


def opened_wallet_id(db_path, capsys):
    assert run(db_path, "open", "--currency", "NGN") == 0
    out = capsys.readouterr().out
    match = re.search(r"opened wallet (\S+)", out)
    assert match, out
    return match.group(1)


def opened_pot(db_path, capsys, wallet_id, name="Vacation", kind="personal", matures=None):
    """Open a pot and swallow its output.

    Returns the name rather than the id, because the name is the handle every
    other ``fund`` command takes - which is the whole point of pots having names.
    """
    argv = ["fund", "open", "--wallet", wallet_id, "--name", name, "--kind", kind]
    if matures is not None:
        argv += ["--matures", matures]
    assert run(db_path, *argv) == 0, capsys.readouterr().err
    capsys.readouterr()
    return name


def a_wallet_with_money(db_path, capsys, amount="10000"):
    """A wallet with ``amount`` sitting in its available balance."""
    wallet_id = opened_wallet_id(db_path, capsys)
    assert run(db_path, "deposit", wallet_id, amount) == 0
    capsys.readouterr()
    return wallet_id


# --- opening and listing ---------------------------------------------------


def test_opening_a_pot_prints_it(tmp_path, capsys):
    db = str(tmp_path / "cli.db")
    wallet_id = opened_wallet_id(db, capsys)

    matures = days_from_now(30)
    assert run(
        db, "fund", "open", "--wallet", wallet_id,
        "--name", "Vacation", "--kind", "personal", "--matures", matures,
    ) == 0
    out = capsys.readouterr().out
    assert f"opened fund 'Vacation' (personal, until {matures}) 0.00 NGN" in out


def test_a_pot_with_no_maturity_says_open(tmp_path, capsys):
    """``None`` renders as "open", not as a blank or an epoch date.

    The distinction the whole migration rests on, said out loud on the command
    line: a pot with no date is a pot that was never sealed, and a user reading
    their own balance should be able to tell that from a pot that came due in
    1970.
    """
    db = str(tmp_path / "cli.db")
    wallet_id = opened_wallet_id(db, capsys)

    opened_pot(db, capsys, wallet_id)

    assert run(db, "fund", "list", wallet_id) == 0
    assert "(personal, open) 0.00 NGN" in capsys.readouterr().out


def test_a_second_pot_with_the_same_name_is_refused(tmp_path, capsys):
    db = str(tmp_path / "cli.db")
    wallet_id = opened_wallet_id(db, capsys)
    opened_pot(db, capsys, wallet_id, name="Vacation")

    assert run(
        db, "fund", "open", "--wallet", wallet_id,
        "--name", "Vacation", "--kind", "personal",
    ) == 1
    err = capsys.readouterr().err
    assert "error:" in err
    assert "Vacation" in err


def test_listing_a_wallets_pots(tmp_path, capsys):
    db = str(tmp_path / "cli.db")
    wallet_id = opened_wallet_id(db, capsys)
    opened_pot(db, capsys, wallet_id, name="Vacation")
    opened_pot(db, capsys, wallet_id, name="Salary", kind="business")

    assert run(db, "fund", "list", wallet_id) == 0
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert "'Vacation'" in lines[0]
    assert "'Salary'" in lines[1]
    assert "(business," in lines[1]


def test_listing_a_wallet_with_no_pots_is_friendly(tmp_path, capsys):
    """Not an error, and not an empty screen either.

    A wallet that never locked anything is the ordinary case rather than a
    problem, so this exits 0 - but it still says *which* wallet it is looking at,
    because "nothing here" and "wrong wallet" look identical otherwise.
    """
    db = str(tmp_path / "cli.db")
    wallet_id = opened_wallet_id(db, capsys)

    assert run(db, "fund", "list", wallet_id) == 0
    assert f"no funds on wallet {wallet_id}" in capsys.readouterr().out


def test_listing_the_pots_of_an_unknown_wallet_is_an_error(tmp_path, capsys):
    db = str(tmp_path / "cli.db")

    assert run(db, "fund", "list", str(uuid4())) == 1
    assert "error:" in capsys.readouterr().err


def test_opening_a_pot_without_a_kind_is_a_usage_error(tmp_path):
    db = str(tmp_path / "cli.db")

    with pytest.raises(SystemExit) as excinfo:
        run(db, "fund", "open", "--wallet", str(uuid4()), "--name", "Vacation")
    assert excinfo.value.code == 2


def test_an_unknown_fund_subcommand_is_a_usage_error(tmp_path):
    db = str(tmp_path / "cli.db")

    with pytest.raises(SystemExit) as excinfo:
        run(db, "fund", "bogus")
    assert excinfo.value.code == 2


# --- the two ways money arrives --------------------------------------------


def test_a_deposit_into_a_pot_comes_from_outside_the_wallet(tmp_path, capsys):
    """``fund deposit`` is not a deposit followed by a lock.

    The distinction worth pinning: the money never lands in the available
    balance, so a pot can be fed by someone who has never put a naira in the
    wallet's spendable half. Available stays at zero and the pot holds it all.
    """
    db = str(tmp_path / "cli.db")
    wallet_id = opened_wallet_id(db, capsys)
    pot = opened_pot(db, capsys, wallet_id)

    assert run(db, "fund", "deposit", wallet_id, pot, "5000") == 0
    out = capsys.readouterr().out
    assert "deposited into 5000.00 NGN" in out
    assert "Vacation 5000.00 NGN" in out
    assert "available 0.00 NGN" in out
    assert "locked 5000.00 NGN" in out


def test_a_lock_moves_money_out_of_the_available_balance(tmp_path, capsys):
    """``fund lock`` moves money the wallet already has - the other direction."""
    db = str(tmp_path / "cli.db")
    wallet_id = a_wallet_with_money(db, capsys)
    pot = opened_pot(db, capsys, wallet_id)

    assert run(db, "fund", "lock", wallet_id, pot, "6000") == 0
    out = capsys.readouterr().out
    assert "locked into 6000.00 NGN" in out
    assert "Vacation 6000.00 NGN" in out
    assert "available 4000.00 NGN" in out
    assert "locked 6000.00 NGN" in out


def test_locking_more_than_the_wallet_holds_is_an_error(tmp_path, capsys):
    db = str(tmp_path / "cli.db")
    wallet_id = a_wallet_with_money(db, capsys, amount="1000")
    pot = opened_pot(db, capsys, wallet_id)

    assert run(db, "fund", "lock", wallet_id, pot, "5000") == 1
    assert "error:" in capsys.readouterr().err

    assert run(db, "balance", wallet_id) == 0
    assert "available: 1000.00 NGN" in capsys.readouterr().out


def test_the_same_reference_does_not_lock_twice(tmp_path, capsys):
    """``--ref`` is the idempotency key, and it survives the trip through argparse.

    A retried command - a shell script run twice, a user pressing up-arrow - must
    move the money once. The second call still reports the pot, because reporting
    the current state is the right answer to "do this again"; it just does not do
    it again.
    """
    db = str(tmp_path / "cli.db")
    wallet_id = a_wallet_with_money(db, capsys)
    pot = opened_pot(db, capsys, wallet_id)

    assert run(db, "fund", "lock", wallet_id, pot, "6000", "--ref", "once") == 0
    capsys.readouterr()
    assert run(db, "fund", "lock", wallet_id, pot, "6000", "--ref", "once") == 0
    assert "Vacation 6000.00 NGN" in capsys.readouterr().out


def test_locking_into_an_unknown_pot_is_an_error(tmp_path, capsys):
    db = str(tmp_path / "cli.db")
    wallet_id = a_wallet_with_money(db, capsys)

    assert run(db, "fund", "lock", wallet_id, "Nope", "1000") == 1
    err = capsys.readouterr().err
    assert "error:" in err
    assert "Nope" in err

    assert run(db, "balance", wallet_id) == 0
    assert "available: 10000.00 NGN" in capsys.readouterr().out


def test_a_frozen_wallet_still_takes_a_deposit_into_a_pot(tmp_path, capsys):
    """Freezing stops value leaving, and this is value arriving."""
    db = str(tmp_path / "cli.db")
    wallet_id = opened_wallet_id(db, capsys)
    pot = opened_pot(db, capsys, wallet_id)
    run(db, "freeze", wallet_id)
    capsys.readouterr()

    assert run(db, "fund", "deposit", wallet_id, pot, "3000") == 0
    assert "locked 3000.00 NGN" in capsys.readouterr().out


def test_a_frozen_wallet_still_allows_a_release(tmp_path, capsys):
    """The money does not leave the wallet - it only changes which half holds it."""
    db = str(tmp_path / "cli.db")
    wallet_id = opened_wallet_id(db, capsys)
    pot = opened_pot(db, capsys, wallet_id, matures=days_from_now(-1))
    run(db, "fund", "deposit", wallet_id, pot, "3000")
    run(db, "freeze", wallet_id)
    capsys.readouterr()

    assert run(db, "fund", "release", wallet_id, pot, "3000") == 0
    out = capsys.readouterr().out
    assert "available 3000.00 NGN" in out
    assert "locked 0.00 NGN" in out


# --- the maturity date, which is what this is all about --------------------


def test_a_sealed_pot_takes_deposits_and_refuses_a_release(tmp_path, capsys):
    """The request that produced this feature, end to end on the command line.

    "If I lock 50,000 I should still be able to add money continuously" - so the
    deposits go in one after another while the pot is sealed, and only the
    release is refused. Both halves matter: a test that only checked the refusal
    would pass on a pot that was simply un-usable.
    """
    db = str(tmp_path / "cli.db")
    wallet_id = opened_wallet_id(db, capsys)
    pot = opened_pot(db, capsys, wallet_id, matures=days_from_now(30))

    for amount in ("50000", "2000", "1500"):
        assert run(db, "fund", "deposit", wallet_id, pot, amount) == 0
    capsys.readouterr()

    assert run(db, "fund", "release", wallet_id, pot, "1000") == 1
    err = capsys.readouterr().err
    assert "error:" in err
    assert "locked until" in err
    assert days_from_now(30) in err

    # And the refusals changed nothing whatsoever.
    assert run(db, "balance", wallet_id) == 0
    out = capsys.readouterr().out
    assert "locked: 53500.00 NGN" in out
    assert "available: 0.00 NGN" in out


def test_releasing_from_a_matured_pot_succeeds(tmp_path, capsys):
    """The other side of the same date: a day past it, the money moves."""
    db = str(tmp_path / "cli.db")
    wallet_id = opened_wallet_id(db, capsys)
    pot = opened_pot(db, capsys, wallet_id, matures=days_from_now(-1))
    run(db, "fund", "deposit", wallet_id, pot, "5000")
    capsys.readouterr()

    assert run(db, "fund", "release", wallet_id, pot, "2000") == 0
    out = capsys.readouterr().out
    assert "released from 2000.00 NGN" in out
    assert "Vacation 3000.00 NGN" in out
    assert "available 2000.00 NGN" in out
    assert "locked 3000.00 NGN" in out


def test_extending_a_matures_date_reseals_the_pot(tmp_path, capsys):
    """Extend is the second half of the promise, and it has to *bind*.

    A pot that has come due and been released from once would be wide open if
    extending only recorded a date. Re-sealing is the point: after the extension
    the same release is refused again, which is what makes extending a real
    commitment rather than a note.
    """
    db = str(tmp_path / "cli.db")
    wallet_id = opened_wallet_id(db, capsys)
    pot = opened_pot(db, capsys, wallet_id, matures=days_from_now(-1))
    run(db, "fund", "deposit", wallet_id, pot, "5000")
    assert run(db, "fund", "release", wallet_id, pot, "1000") == 0
    capsys.readouterr()

    later = days_from_now(60)
    assert run(db, "fund", "extend", wallet_id, pot, "--to", later) == 0
    assert f"extended fund 'Vacation' (personal, until {later}) 4000.00 NGN" in (
        capsys.readouterr().out
    )

    assert run(db, "fund", "release", wallet_id, pot, "1000") == 1
    assert "locked until" in capsys.readouterr().err


def test_extending_an_open_pot_gives_it_a_date(tmp_path, capsys):
    """An open pot can be sealed, which is how the migrated one becomes real.

    A pot with no date is always releasable. Setting a date on it is the only way
    to make it a commitment, so it is allowed - the refusal is only against a
    date that is *earlier* than the current one.
    """
    db = str(tmp_path / "cli.db")
    wallet_id = opened_wallet_id(db, capsys)
    pot = opened_pot(db, capsys, wallet_id)
    run(db, "fund", "deposit", wallet_id, pot, "5000")
    assert run(db, "fund", "release", wallet_id, pot, "1000") == 0
    capsys.readouterr()

    later = days_from_now(60)
    assert run(db, "fund", "extend", wallet_id, pot, "--to", later) == 0
    capsys.readouterr()

    assert run(db, "fund", "release", wallet_id, pot, "1000") == 1
    assert "locked until" in capsys.readouterr().err


def test_extending_to_an_earlier_date_is_refused(tmp_path, capsys):
    """Moving a date earlier is an early release wearing a different hat."""
    db = str(tmp_path / "cli.db")
    wallet_id = opened_wallet_id(db, capsys)
    pot = opened_pot(db, capsys, wallet_id, matures=days_from_now(90))

    assert run(db, "fund", "extend", wallet_id, pot, "--to", days_from_now(30)) == 1
    assert "error:" in capsys.readouterr().err

    # And the original date still stands, so nothing was quietly moved.
    assert run(db, "fund", "list", wallet_id) == 0
    assert days_from_now(90) in capsys.readouterr().out


def test_extending_into_the_past_is_refused_by_the_domain(tmp_path, capsys):
    """A date the parser accepts and the aggregate refuses - and it *is* refused.

    Worth separating from the ``--to`` parse error, because the two produce
    different exit codes and different feelings. ``--to yesterday`` is a
    perfectly well-formed date, so the parse succeeds and the failure comes from
    the domain rule - exit 1 with an ``error:`` line, rather than the exit 2 of a
    bad flag.
    """
    db = str(tmp_path / "cli.db")
    wallet_id = opened_wallet_id(db, capsys)
    pot = opened_pot(db, capsys, wallet_id, matures=days_from_now(90))

    assert run(db, "fund", "extend", wallet_id, pot, "--to", days_from_now(-1)) == 1
    assert "error:" in capsys.readouterr().err


def test_extending_an_unknown_pot_is_an_error(tmp_path, capsys):
    db = str(tmp_path / "cli.db")
    wallet_id = opened_wallet_id(db, capsys)

    assert run(
        db, "fund", "extend", wallet_id, "Nope", "--to", days_from_now(30)
    ) == 1
    assert "error:" in capsys.readouterr().err


def test_a_bad_maturity_date_is_a_usage_error(tmp_path):
    db = str(tmp_path / "cli.db")

    with pytest.raises(SystemExit) as excinfo:
        run(
            db, "fund", "open", "--wallet", str(uuid4()),
            "--name", "Vacation", "--kind", "personal", "--matures", "next tuesday",
        )
    assert excinfo.value.code == 2


# --- balance, which now shows the breakdown --------------------------------


def test_balance_shows_the_pots_under_the_totals(tmp_path, capsys):
    """The derived total and the breakdown have to be readable together.

    ``locked`` is *defined* as the sum of the lines beneath it, so printing the
    two side by side is what lets a user check the arithmetic that the number is.
    """
    db = str(tmp_path / "cli.db")
    wallet_id = a_wallet_with_money(db, capsys)
    opened_pot(db, capsys, wallet_id, name="Vacation")
    opened_pot(db, capsys, wallet_id, name="Festive")
    run(db, "fund", "lock", wallet_id, "Vacation", "2000")
    run(db, "fund", "lock", wallet_id, "Festive", "1500")
    capsys.readouterr()

    assert run(db, "balance", wallet_id) == 0
    out = capsys.readouterr().out
    assert "available: 6500.00 NGN" in out
    assert "locked: 3500.00 NGN" in out
    assert "funds:" in out
    assert "'Vacation' (personal, open) 2000.00 NGN" in out
    assert "'Festive' (personal, open) 1500.00 NGN" in out


def test_balance_of_a_wallet_with_no_pots_shows_no_heading(tmp_path, capsys):
    """The ordinary case stays clean - an empty "funds:" heading is noise.

    And with no pots the locked line has to read zero rather than going missing,
    because the sum over an empty collection is a real zero and not an absence.
    """
    db = str(tmp_path / "cli.db")
    wallet_id = opened_wallet_id(db, capsys)

    assert run(db, "balance", wallet_id) == 0
    out = capsys.readouterr().out
    assert "locked: 0.00 NGN" in out
    assert "funds:" not in out
