"""``reconcile`` at the terminal - the verb, and the fact that it needs nobody.

Two things are being tested and they are different sizes. The small one is that
the command exists and prints its run: one line per row it asked about, one line
when it asked about nothing, and an exit 0 either way. The large one is that it
is *actorless* - it runs on a machine where nobody has ever logged in, which is
the property that makes it a cron line rather than something a person remembers
to do, and the property a future change that resolved an actor first would take
away.

**The double is at the socket, not at the provider**, and that is a deliberate
choice about what this file is for. ``build_reconciler`` has a provider parameter
and the CLI deliberately does not use it: ``_reconcile_command`` builds the
adapter from the environment, so a test-only way to hand it a different one would
be a way to run the command configured as no deployment can be. So the whole path
is exercised for real - argument parsing, dispatch ahead of the actor line, the
settings read, the composition root, the adapter - and exactly one thing is
replaced: ``httpx.request``, which is where the socket would be.

The double is a second small copy of the one in
``tests/infrastructure/payments/test_paystack_payment_provider.py`` rather than an
import from it. That file's copy is about what goes on the wire and records
everything; this one answers one charge and exists so the suite has no network.
Sharing it would mean this file depending on a module it has nothing else to do
with - the same argument that keeps ``FakePaymentProvider`` in ``conftest.py``
and that recorder out of it.
"""

import re
from datetime import datetime
from decimal import Decimal
from uuid import UUID

import httpx
import pytest

from app.domain.money.currency import Currency
from app.domain.money.money import Money
from app.domain.money.transaction import Transaction
from app.domain.money.transactionType import TransactionType
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)
from app.presentation.cli import main
from tests.conftest import TEST_PAYSTACK_SECRET, session_path_for, signed_in

#: Where a seeded deposit was made, and the moment the runs below are run at.
#: Both are fixed rather than read from a clock for the reason the use case takes
#: an ``as_of`` at all: a test that wanted a row past the grace window would
#: otherwise have to wait for it.
EARLIER = "2026-01-02T11:00"
AS_OF = "2026-01-02T12:00"


class AnsweringPaystack:
    """Stands in for ``httpx.request``, so this file never opens a socket.

    Answers one charge, described by the two fields the adapter reads: its status
    and its amount in kobo. Everything else about the response is trimmed away on
    purpose - a double carrying fields nothing reads would let a test believe it
    had covered something it had not.

    Records the method and the URL, because "this command asked about the right
    reference" is a claim worth being able to make from a test that is otherwise
    about the command existing.
    """

    def __init__(self, charge="success", amount=500000):
        self.calls: list[dict] = []
        self.body = {
            "status": True,
            "data": {"status": charge, "amount": amount, "reference": "dep-1"},
        }

    def __call__(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url})
        return httpx.Response(200, json=self.body)


@pytest.fixture
def answering(monkeypatch):
    """Replace ``httpx.request`` for one test, and hand back the recorder."""
    recorder = AnsweringPaystack()
    monkeypatch.setattr(
        "app.infrastructure.payments.paystack_payment_provider.httpx.request",
        recorder,
    )
    return recorder


def run(db_path, *argv):
    return main(["--db", db_path, "--session", session_path_for(db_path), *argv])


def opened_wallet_id(db_path, capsys) -> str:
    """Create a wallet through the CLI, and return the id it printed.

    Through the command rather than by handing this file a fixture-built wallet,
    and that is not a preference. ``signed_in`` registers an account and signup
    mints its own user id, so a wallet built by ``build_wallet`` would belong to
    ``TEST_USER_ID`` - an owner no command in this file can act as, since every
    one of them resolves the actor from the session. The first version of this
    file seeded the wallet directly and the ``balance`` read failed with "no such
    wallet" for exactly that reason.
    """
    assert run(db_path, "open", "--currency", "NGN") == 0
    out = capsys.readouterr().out
    match = re.search(r"opened wallet (\S+)", out)
    assert match, out
    return match.group(1)


def seed_deposit(db_path, wallet_id, reference="dep-1"):
    """Put one provider-backed PENDING row in the ledger the CLI will read.

    Directly rather than through a command, and it has to be: the CLI's own
    deposit command moves money locally and never opens a collection with a
    provider, so it cannot write the one row this job exists to find. A
    provider-backed PENDING row is the state ``InitiateDeposit`` leaves, and the
    state every test in this file has to start from.
    """
    factory = SqliteUnitOfWorkFactory(db_path)
    uow = factory.start()
    uow.transactions.save(a_deposit(UUID(wallet_id), reference))
    uow.commit()


def seed_wallet(db_path, wallet, *rows):
    """Put a wallet and its rows in the store, for the tests with no session.

    The no-session tests cannot create a wallet through the CLI - ``open``
    resolves an actor like every other wallet command - so they build one here
    and read it back as its own owner. One unit, because ``transactions.wallet_id``
    is a real foreign key.
    """
    factory = SqliteUnitOfWorkFactory(db_path)
    uow = factory.start()
    uow.wallets.save(wallet)
    for row in rows:
        uow.transactions.save(row)
    uow.commit()


def stored_balance(db_path, wallet):
    """The balance the CLI's own database holds, read as the wallet's owner."""
    factory = SqliteUnitOfWorkFactory(db_path)
    uow = factory.start()
    try:
        return uow.wallets.get_owned(wallet.wallet_id, wallet.user_id).available_balance
    finally:
        uow.rollback()


def balance_text(db_path, capsys, wallet_id) -> str:
    """What the CLI says this wallet holds - the owner's own view of a credit.

    The buffer is emptied first, because a test that has already run ``deposit``
    and ``reconcile`` has their output pending - and ``deposit`` prints a balance
    too, so a search over the accumulated text would happily return the balance
    from *before* the recovery and report the test as passing. Found by writing
    it the other way and noticing the assertion could be satisfied by the wrong
    command's output.
    """
    capsys.readouterr()
    assert run(db_path, "balance", wallet_id) == 0
    out = capsys.readouterr().out
    match = re.search(r"available: (\S+ \S+)", out)
    assert match, out
    return match.group(1)


def a_deposit(wallet_id, reference="dep-1"):
    """A deposit as it stands when its webhook never arrived.

    PENDING, provider-named, and made at a fixed moment well before every run
    below - which is the only state this job is ever asked about.
    """
    return Transaction(
        wallet_id=wallet_id,
        type=TransactionType.DEPOSIT,
        amount=Money(Decimal("5000"), Currency.NGN),
        internal_reference=f"{wallet_id}:{reference}",
        provider_reference=reference,
        created_at=datetime.fromisoformat(EARLIER),
    )


class TestARunThatFindsWork:
    def test_a_lost_webhook_is_recovered_from_the_command_line(
        self, tmp_path, capsys, monkeypatch, answering
    ):
        """**The whole feature, entered the way an operator enters it.**

        A wallet opened through the CLI, a deposit whose webhook never landed,
        and then the command: by the time it returns, the money is in the wallet
        and the log says so. The output line is asserted as well as the balance,
        because a recovery nobody can see in the log is a recovery nobody can
        audit - and the URL is asserted because "it asked about the right
        reference" is otherwise invisible from here.
        """
        db = str(tmp_path / "cli.db")
        signed_in(db)
        wallet_id = opened_wallet_id(db, capsys)
        seed_deposit(db, wallet_id)
        monkeypatch.setenv("PAYSTACK_SECRET_KEY", TEST_PAYSTACK_SECRET)

        assert run(db, "reconcile", "--as-of", AS_OF) == 0

        out = capsys.readouterr().out
        assert "dep-1" in out
        assert "settled" in out
        assert balance_text(db, capsys, wallet_id) == "5000.00 NGN"
        assert answering.calls[0]["url"].endswith("/transaction/verify/dep-1")

    def test_the_credited_balance_is_the_one_the_wallet_command_reports(
        self, tmp_path, capsys, monkeypatch, answering
    ):
        """**Read back through the wallet command, which is the owner's view.**

        The store assertions in ``test_reconcile_payments.py`` already prove the
        credit landed in the right wallet; what this adds is that the *command
        line* agrees - the credit is visible to the person whose money it is,
        through the same door every other balance is read by. A recovery that
        moved money only in the store would be a recovery nobody could spend.
        """
        db = str(tmp_path / "cli.db")
        signed_in(db)
        wallet_id = opened_wallet_id(db, capsys)
        assert run(db, "deposit", wallet_id, "1000") == 0
        seed_deposit(db, wallet_id)
        monkeypatch.setenv("PAYSTACK_SECRET_KEY", TEST_PAYSTACK_SECRET)

        run(db, "reconcile", "--as-of", AS_OF)

        assert balance_text(db, capsys, wallet_id) == "6000.00 NGN"

    def test_a_run_with_no_as_of_uses_the_default(
        self, tmp_path, capsys, monkeypatch, answering
    ):
        """The command as cron actually calls it: no moment, no arguments.

        The default is today at midnight, and the seed is deliberately ancient -
        the point is not the window but that the argument is optional, because a
        job that had to be told what time it was would be a job somebody has to
        write a shell expression for.
        """
        db = str(tmp_path / "cli.db")
        signed_in(db)
        wallet_id = opened_wallet_id(db, capsys)
        seed_deposit(db, wallet_id)
        monkeypatch.setenv("PAYSTACK_SECRET_KEY", TEST_PAYSTACK_SECRET)

        assert run(db, "reconcile") == 0

        assert "settled" in capsys.readouterr().out

    def test_a_provider_that_says_nothing_has_arrived_moves_nothing(
        self, tmp_path, capsys, monkeypatch, answering
    ):
        """The ordinary answer, reported rather than treated as a failure.

        A payer still on the checkout page produces this on every run, so an
        operator who read it as an error would be reading an error every hour
        about a payment that is simply not finished yet. Exit 0 is the assertion
        that says so.
        """
        db = str(tmp_path / "cli.db")
        signed_in(db)
        wallet_id = opened_wallet_id(db, capsys)
        assert run(db, "deposit", wallet_id, "1000") == 0
        seed_deposit(db, wallet_id)
        monkeypatch.setenv("PAYSTACK_SECRET_KEY", TEST_PAYSTACK_SECRET)
        answering.body = {
            "status": True,
            "data": {"status": "abandoned", "amount": 500000, "reference": "dep-1"},
        }

        assert run(db, "reconcile", "--as-of", AS_OF) == 0

        out = capsys.readouterr().out
        assert "dep-1" in out
        assert "not_settled" in out
        assert balance_text(db, capsys, wallet_id) == "1000.00 NGN"


class TestARunThatFindsNothing:
    def test_a_quiet_run_prints_one_line_and_succeeds(
        self, tmp_path, capsys, monkeypatch, answering
    ):
        """**A quiet run still speaks, which is the opposite of the drains.**

        "Nothing was in flight" and "this job is not running" look identical
        from cron's output otherwise, and the first is exactly what an operator
        wants to see - it means the webhook path is keeping up. So the line is
        printed rather than the silence a drain would keep.
        """
        db = str(tmp_path / "cli.db")
        signed_in(db)
        monkeypatch.setenv("PAYSTACK_SECRET_KEY", TEST_PAYSTACK_SECRET)

        assert run(db, "reconcile", "--as-of", AS_OF) == 0

        assert "nothing in flight" in capsys.readouterr().out
        assert answering.calls == []

    def test_a_run_with_no_key_says_so_once_and_succeeds(self, tmp_path, capsys):
        """**The missing-mailbox rule, one provider over.**

        With no ``PAYSTACK_SECRET_KEY`` there is nothing to ask with - the
        ordinary state of a deployment that does not take card payments - so a
        job that failed loudly here would be a cron line mailing an operator
        every hour about a configuration that is not wrong. It says so once, so
        that somebody who *expected* reconciliation to be running finds out it is
        not, and stops.

        The variable is named, which is ``describe_configuration``'s rule: the
        failure mode of a job with nothing to ask with is silence, and naming the
        variable is the difference between a two-minute fix and an afternoon of
        guessing.
        """
        db = str(tmp_path / "cli.db")
        signed_in(db)

        assert run(db, "reconcile", "--as-of", AS_OF) == 0

        out = capsys.readouterr().out
        assert "note:" in out
        assert "PAYSTACK_SECRET_KEY is not set" in out


class TestTheCommandNeedsNobody:
    """The property that makes this a cron line rather than a habit.

    Every other command except the identity three and ``plan tick`` resolves an
    actor before it does anything, and the ordering in ``main`` is the design:
    resolving one first would have made this a job that requires somebody to be
    logged in, which on a server is a job that stops running the first time a
    token expires.
    """

    def test_it_runs_with_no_session_file_at_all(self, tmp_path, capsys):
        """**No account, no token, no file - and exit 0.**

        The session path is passed and does not exist, which is every fresh
        deployment: nobody has signed in on this machine and nobody needs to.
        This is the assertion a future change that moved the dispatch below the
        actor line would fail, and it would fail with "not signed in" - a message
        that says nothing about why reconciliation stopped.
        """
        db = str(tmp_path / "cli.db")

        assert (
            main(
                [
                    "--db",
                    db,
                    "--session",
                    str(tmp_path / "never-written.session"),
                    "reconcile",
                    "--as-of",
                    AS_OF,
                ]
            )
            == 0
        )

        assert "note:" in capsys.readouterr().out

    def test_it_recovers_a_payment_with_nobody_signed_in(
        self, tmp_path, capsys, monkeypatch, build_wallet, answering
    ):
        """The same claim with money in it: no session file, and the credit lands.

        Worth its own test because the quiet version above could pass on a
        command that did nothing at all. Here the row is seeded, the provider
        answers, and a wallet the CLI never named is credited - which is
        ``owner_of`` deriving the owner from the ledger row, with no actor
        anywhere in the invocation. The wallet is built by a fixture rather than
        opened through the CLI, because there is no session for ``open`` to act
        as - which is the point being made.
        """
        db = str(tmp_path / "cli.db")
        wallet = build_wallet(available="0")
        seed_wallet(db, wallet, a_deposit(wallet.wallet_id))
        monkeypatch.setenv("PAYSTACK_SECRET_KEY", TEST_PAYSTACK_SECRET)

        assert (
            main(
                [
                    "--db",
                    db,
                    "--session",
                    str(tmp_path / "never-written.session"),
                    "reconcile",
                    "--as-of",
                    AS_OF,
                ]
            )
            == 0
        )

        assert stored_balance(db, wallet).amount == 5000
