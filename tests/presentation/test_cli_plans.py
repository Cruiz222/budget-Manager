"""The plan commands, exercised the way a user reaches them.

Everything here goes through ``main(argv)`` against a real SQLite file, so the
tests cover the whole path: argument parsing, the service, the aggregate and the
store. That is deliberate for a presentation layer - the interesting failures
are at the seams, and a test that called the service directly would step over
exactly the seam it was meant to check.
"""

import re
from uuid import uuid4

import pytest

from app.application.notifications.deliver_notifications import DeliverNotifications
from app.application.notifications.deliver_pending_messages import (
    DeliverPendingMessages,
)
from app.presentation import cli
from app.presentation.cli import main


def run(db_path, *argv):
    """Invoke the CLI in-process against the given database file."""
    return main(["--db", db_path, *argv])


def install_deliverer(monkeypatch, channel):
    """Wire a channel into the CLI in place of the one it builds from the environment.

    ``build_deliverer`` is the single place the CLI turns the environment into
    something that can reach the outside world for *warnings*, which makes it the
    single place worth replacing. Everything underneath - the drain, the expiry
    rule, the failure handling - is the real thing; only the wire is fake.

    ``channel`` may be ``None``, which is not "a channel that fails" but the
    absence of one - the state an install is in with no email configured.
    """
    def build(*args, **kwargs):
        return DeliverPendingMessages(
            kwargs["unit_of_work_factory"], channel=channel
        )

    monkeypatch.setattr(cli, "build_deliverer", build)


def install_notification_deliverer(monkeypatch, channel):
    """The same swap, for the *receipt* queue one drain over.

    A second injection point because there is a second drain, and the two are
    genuinely separate use cases rather than one with a flag - see
    ``DeliverNotifications``. A test that is about receipts calls this to say
    where they go; every other test in this file gets the default installed by
    ``receipts_go_somewhere_else`` below.
    """
    def build(*args, **kwargs):
        return DeliverNotifications(
            kwargs["unit_of_work_factory"], channel=channel
        )

    monkeypatch.setattr(cli, "build_notification_deliverer", build)


@pytest.fixture(autouse=True)
def receipts_go_somewhere_else(monkeypatch, build_channel):
    """Keep the receipt queue off the wire a warning test is counting on.

    Every test in this file that configures email does so to test the *warning*,
    and the two are not the same queue. The wallet helpers underneath them now
    queue receipts of their own - ``funded_locked_wallet`` deposits, and a deposit
    is an announced event - so leaving the receipt drain on the real builder would
    mean an unconfigured-in-test SMTP adapter in every such test, and leaving it
    on the *warning* channel would make ``len(channel.sent) == 1`` a number about
    two features at once.

    So receipts are delivered here, to a channel this fixture makes fresh for each
    test. That is the same move conftest makes for the environment: the point is
    that "the queue under test is the only one on the wire" is a property of the
    file rather than something every test has to remember.
    """
    install_notification_deliverer(monkeypatch, build_channel())


def opened_wallet_id(db_path, capsys, currency="NGN"):
    assert run(db_path, "open", "--currency", currency) == 0
    out = capsys.readouterr().out
    match = re.search(r"opened wallet (\S+)", out)
    assert match, out
    return match.group(1)


def opened_wallet_with_pot(db_path, capsys, name="Savings", kind="personal"):
    """A wallet with one *empty* pot - the least a locked plan can name.

    Split out of ``funded_locked_wallet`` because two of the plan tests want the
    pot without the money: a locked-source plan must name a pot, but nothing says
    the pot has to hold anything, and "the plan exists before it is affordable"
    is one of the product's rules. Building it from the funded fixture would
    quietly fund those tests too and turn a test about planning into a test about
    paying.
    """
    wallet_id = opened_wallet_id(db_path, capsys)
    assert run(
        db_path, "fund", "open", "--wallet", wallet_id,
        "--name", name, "--kind", kind,
    ) == 0
    capsys.readouterr()
    return wallet_id


def funded_locked_wallet(db_path, capsys, amount="100000"):
    """A wallet with ``amount`` sitting in an open pot called "Savings".

    Depositing, opening a pot, and then locking into it - because the three are
    separate operations, and the plan has to spend money that is actually in a
    pot. The pot has no maturity date, so it is spendable at every moment: the
    plan tests below are about plans, and a sealed pot here would make every one
    of them a test about maturity instead.
    """
    wallet_id = opened_wallet_with_pot(db_path, capsys)
    assert run(db_path, "deposit", wallet_id, amount) == 0
    assert run(db_path, "fund", "lock", wallet_id, "Savings", amount) == 0
    capsys.readouterr()
    return wallet_id


def salary_lines(amount="20000"):
    return ("--pay", amount, "0123456789", "058", "Chinedu Okafor", "salary")


#: A whole UUID at the start of a line. Matching the full shape rather than
#: "the first token" matters because these tests run several commands in a row
#: and the capture buffer can still hold earlier output - `re.search(r"^\S+")`
#: would happily return the word "created".
PLAN_ID_LINE = re.compile(
    r"^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\s",
    re.MULTILINE,
)


def create_salary_plan(db_path, wallet_id, start="2026-01-01", *extra, pot="Savings"):
    """Create the headline plan and return the exit code.

    ``--from-fund`` is not optional in spirit: a locked-source plan must name the
    pot it draws from, so a helper that left it out would be testing a refusal
    rather than a plan. ``Savings`` is the pot ``funded_locked_wallet`` opens, so
    the default pairs this helper with that fixture.

    Deliberately does *not* touch ``capsys``. It used to clear the capture on
    the way out, which made the two tests that assert on a *failed* create see
    an empty buffer - the helper had eaten the error message they came to check.
    A helper that hides output is a helper that hides failures.
    """
    return run(
        db_path,
        "plan",
        "create",
        "--wallet",
        wallet_id,
        "--name",
        "Salary 2026",
        "--source",
        "locked",
        "--from-fund",
        pot,
        "--every",
        "monthly",
        "--from",
        start,
        *salary_lines(),
        *extra,
    )


def plan_id_from(db_path, wallet_id, capsys):
    assert run(db_path, "plan", "list", wallet_id) == 0
    out = capsys.readouterr().out
    match = PLAN_ID_LINE.search(out)
    assert match, out
    return match.group(1)


class TestCreating:
    def test_create_then_list(self, tmp_path, capsys):
        db = str(tmp_path / "cli.db")
        wallet_id = funded_locked_wallet(db, capsys)

        assert create_salary_plan(db, wallet_id) == 0
        capsys.readouterr()

        assert run(db, "plan", "list", wallet_id) == 0
        out = capsys.readouterr().out
        assert "Salary 2026" in out
        assert "active" in out
        assert "next 2026-01-01" in out

    def test_the_total_is_what_one_run_costs(self, tmp_path, capsys):
        db = str(tmp_path / "cli.db")
        wallet_id = funded_locked_wallet(db, capsys)

        run(
            db,
            "plan",
            "create",
            "--wallet",
            wallet_id,
            "--name",
            "split",
            "--source",
            "locked",
            "--from-fund",
            "Savings",
            "--every",
            "monthly",
            "--from",
            "2026-01-01",
            "--pay",
            "20000",
            "0123456789",
            "058",
            "Chinedu Okafor",
            "salary",
            "--pay",
            "15000",
            "0987654321",
            "058",
            "Ada Nwosu",
            "rent",
        )
        capsys.readouterr()

        assert run(db, "plan", "list", wallet_id) == 0
        assert "35000.00 NGN" in capsys.readouterr().out

    def test_a_plan_can_be_created_before_it_is_affordable(self, tmp_path, capsys):
        """Nothing is locked; the plan is still created.

        The counterpart to the service test: this is the path a user actually
        takes when planning to save up.
        """
        db = str(tmp_path / "cli.db")
        wallet_id = opened_wallet_with_pot(db, capsys)

        assert create_salary_plan(db, wallet_id) == 0
        # Discard the create's own output. Without this the assertion below
        # would pass on the "created ..." line from two statements ago, and the
        # test would no longer be about `plan list` at all.
        capsys.readouterr()

        assert run(db, "plan", "list", wallet_id) == 0
        assert "Salary 2026" in capsys.readouterr().out

    def test_amounts_are_read_in_the_wallets_currency(self, tmp_path, capsys):
        """A currency mismatch is unrepresentable from here, and that is a
        feature rather than an accident: bare numbers on this CLI carry no
        currency, so they are read in the wallet's, which is the currency the
        plan is then required to be in. The rule can still be broken - the
        service test proves it - but it cannot be broken by typing."""
        db = str(tmp_path / "cli.db")
        wallet_id = opened_wallet_id(db, capsys, currency="USD")
        run(db, "plan", "create", "--wallet", wallet_id, "--name", "USD plan",
            "--source", "available", "--every", "monthly", "--from", "2026-01-01",
            *salary_lines())
        capsys.readouterr()

        assert run(db, "plan", "list", wallet_id) == 0
        assert "20000.00 USD" in capsys.readouterr().out


class TestNamingThePot:
    """``--from-fund``, and the two combinations the service refuses.

    The flag is ordinary argparse, but *when* it is required is not: it is
    required exactly when the source is ``locked``. That makes it the one
    argument on this command whose validity depends on another argument's value,
    so it is worth pinning from the outside rather than trusting the service
    tests to describe what a user sees.
    """

    def test_a_locked_plan_names_its_pot_and_show_says_so(self, tmp_path, capsys):
        """The round trip a user actually makes: create it, then look at it.

        ``plan show`` printing ``pot:`` is the entire payoff of Phase B for
        somebody at a terminal. The plan holds a ``fund_id`` - correct for the
        aggregate, useless to a human - so the command resolves it to the name,
        and that translation has no other way to be observed.
        """
        db = str(tmp_path / "cli.db")
        wallet_id = funded_locked_wallet(db, capsys)

        assert create_salary_plan(db, wallet_id) == 0
        capsys.readouterr()
        plan_id = plan_id_from(db, wallet_id, capsys)
        capsys.readouterr()

        assert run(db, "plan", "show", plan_id) == 0
        assert "pot: Savings" in capsys.readouterr().out

    def test_a_locked_plan_must_name_a_pot(self, tmp_path, capsys):
        """Omitting the flag is refused, and the refusal says what to do.

        Not an argparse error - the wallet has to be read before it can be known
        that a locked plan needs a pot at all, and argparse cannot see that. So
        this is a domain refusal: exit 1 with a message, not exit 2 with a usage
        block.
        """
        db = str(tmp_path / "cli.db")
        wallet_id = funded_locked_wallet(db, capsys)

        assert run(db, "plan", "create", "--wallet", wallet_id,
                   "--name", "Salary 2026", "--source", "locked",
                   "--every", "monthly", "--from", "2026-01-01",
                   *salary_lines()) == 1
        assert "must name the pot" in capsys.readouterr().err

    def test_an_available_plan_may_not_name_a_pot(self, tmp_path, capsys):
        """The other direction, and it is not merely redundant - it is wrong.

        An available-balance plan spends the wallet's spendable money, which is
        not in any pot. Accepting the flag would record a commitment the run
        could never honour, and the user would find that out when the payout
        quietly came from somewhere else.
        """
        db = str(tmp_path / "cli.db")
        wallet_id = opened_wallet_with_pot(db, capsys)

        assert run(db, "plan", "create", "--wallet", wallet_id,
                   "--name", "Salary 2026", "--source", "available",
                   "--from-fund", "Savings",
                   "--every", "monthly", "--from", "2026-01-01",
                   *salary_lines()) == 1
        assert "only a plan that spends the locked balance" in capsys.readouterr().err

    def test_an_unknown_pot_is_refused_at_creation(self, tmp_path, capsys):
        """Named rather than discovered later, which is the point of naming it.

        The check happens while the wallet is in hand at creation time. The
        alternative - accepting any string and failing on the first tick - would
        turn a typo into a payment that silently does not go out.
        """
        db = str(tmp_path / "cli.db")
        wallet_id = funded_locked_wallet(db, capsys)

        assert create_salary_plan(db, wallet_id, pot="No Such Pot") == 1
        assert "error:" in capsys.readouterr().err

    def test_an_available_plan_shows_the_pooled_draw(self, tmp_path, capsys):
        """``(pooled)`` rather than a blank line, for a plan that names no pot.

        A plan with no pot is not a plan with a missing value. An available plan
        never had one, and holds none permanently - so the line has to say
        something true and legible rather than render as an empty field.
        """
        db = str(tmp_path / "cli.db")
        wallet_id = opened_wallet_id(db, capsys)

        assert run(db, "plan", "create", "--wallet", wallet_id, "--name", "Spends",
                   "--source", "available", "--every", "monthly",
                   "--from", "2026-01-01", *salary_lines()) == 0
        capsys.readouterr()
        plan_id = plan_id_from(db, wallet_id, capsys)
        capsys.readouterr()

        assert run(db, "plan", "show", plan_id) == 0
        assert "pot: (pooled)" in capsys.readouterr().out

    def test_plan_edit_cannot_change_the_pot(self, tmp_path, capsys):
        """The flag is absent, so argparse refuses it before anything runs.

        Deliberate, and the reason is the rule itself: pointing an existing
        commitment at a different pot is the redirect the whole commitment test
        exists to stop, arriving through the one door that test cannot see -
        the new pot would be judged against the plan's *old* creation moment.
        Getting it wrong is recoverable, since cancelling frees nothing, but it
        is not something to offer a user.
        """
        db = str(tmp_path / "cli.db")

        with pytest.raises(SystemExit) as excinfo:
            run(db, "plan", "edit", str(uuid4()), "--from-fund", "Savings")
        assert excinfo.value.code == 2


class TestTheTerm:
    def test_for_resolves_to_an_end_date(self, tmp_path, capsys):
        db = str(tmp_path / "cli.db")
        wallet_id = funded_locked_wallet(db, capsys)
        create_salary_plan(db, wallet_id, "2026-01-01", "--for", "12", "months")
        plan_id = plan_id_from(db, wallet_id, capsys)
        capsys.readouterr()

        assert run(db, "plan", "show", plan_id) == 0
        assert "ends on: 2027-01-01" in capsys.readouterr().out

    def test_for_counts_months_not_days(self, tmp_path, capsys):
        """"3 months" from 31 January ends on 30 April, not 1 May.

        The whole reason DurationUnit has four members instead of one. A day
        count would have to guess, and would guess wrong at month ends.
        """
        db = str(tmp_path / "cli.db")
        wallet_id = funded_locked_wallet(db, capsys)
        create_salary_plan(db, wallet_id, "2026-01-31", "--for", "3", "months")
        plan_id = plan_id_from(db, wallet_id, capsys)
        capsys.readouterr()

        assert run(db, "plan", "show", plan_id) == 0
        assert "ends on: 2026-04-30" in capsys.readouterr().out

    def test_until_sets_the_end_date_directly(self, tmp_path, capsys):
        db = str(tmp_path / "cli.db")
        wallet_id = funded_locked_wallet(db, capsys)
        create_salary_plan(db, wallet_id, "2026-01-01", "--until", "2026-07-01")
        plan_id = plan_id_from(db, wallet_id, capsys)
        capsys.readouterr()

        assert run(db, "plan", "show", plan_id) == 0
        assert "ends on: 2026-07-01" in capsys.readouterr().out

    def test_a_plan_without_a_term_never_ends(self, tmp_path, capsys):
        db = str(tmp_path / "cli.db")
        wallet_id = funded_locked_wallet(db, capsys)
        create_salary_plan(db, wallet_id)
        plan_id = plan_id_from(db, wallet_id, capsys)
        capsys.readouterr()

        assert run(db, "plan", "show", plan_id) == 0
        assert "ends on: never" in capsys.readouterr().out

    def test_until_and_for_together_are_a_usage_error(self, tmp_path):
        db = str(tmp_path / "cli.db")

        with pytest.raises(SystemExit) as excinfo:
            run(db, "plan", "create", "--wallet", str(uuid4()), "--name", "x",
                "--source", "locked", "--every", "monthly",
                "--until", "2026-07-01", "--for", "3", "months", *salary_lines())
        assert excinfo.value.code == 2

    def test_a_zero_term_is_refused_by_the_domain(self, tmp_path, capsys):
        """Exit code 1, not 2, and that distinction is the point.

        argparse owns *shape* errors and exits 2 with a usage message. "A term
        must be positive" is a domain rule, so it comes back as a MoneyError,
        which main already renders as ``error: ...`` with exit code 1.
        """
        db = str(tmp_path / "cli.db")
        wallet_id = funded_locked_wallet(db, capsys)

        assert create_salary_plan(
            db, wallet_id, "2026-01-01", "--for", "0", "months"
        ) == 1
        assert "error:" in capsys.readouterr().err

    def test_an_unknown_unit_lists_the_valid_ones(self, tmp_path, capsys):
        db = str(tmp_path / "cli.db")
        wallet_id = funded_locked_wallet(db, capsys)

        assert create_salary_plan(
            db, wallet_id, "2026-01-01", "--for", "3", "fortnights"
        ) == 1
        err = capsys.readouterr().err
        assert "error:" in err
        assert "days, weeks, months, years" in err


class TestTicking:
    def test_a_due_plan_pays_and_the_balance_drops(self, tmp_path, capsys):
        db = str(tmp_path / "cli.db")
        wallet_id = funded_locked_wallet(db, capsys)
        create_salary_plan(db, wallet_id)

        assert run(db, "plan", "tick", "--as-of", "2026-01-01") == 0
        out = capsys.readouterr().out
        assert "2026-01-01" in out
        assert "succeeded" in out

        assert run(db, "balance", wallet_id) == 0
        assert "locked: 80000.00 NGN" in capsys.readouterr().out

    def test_the_payout_reaches_the_ledger(self, tmp_path, capsys):
        db = str(tmp_path / "cli.db")
        wallet_id = funded_locked_wallet(db, capsys)
        create_salary_plan(db, wallet_id)
        run(db, "plan", "tick", "--as-of", "2026-01-01")
        capsys.readouterr()

        assert run(db, "history", wallet_id) == 0
        out = capsys.readouterr().out
        assert "payout" in out
        assert "20000.00 NGN" in out

    def test_nothing_due_is_not_a_failure(self, tmp_path, capsys):
        """The normal case for a cron job that runs far more often than plans
        come due. Exiting non-zero for "there was nothing to do" would make
        every quiet tick look like an incident."""
        db = str(tmp_path / "cli.db")
        wallet_id = funded_locked_wallet(db, capsys)
        create_salary_plan(db, wallet_id)

        assert run(db, "plan", "tick", "--as-of", "2026-01-01") == 0
        capsys.readouterr()
        assert run(db, "plan", "tick", "--as-of", "2026-01-01") == 0
        assert "nothing due" in capsys.readouterr().out

    def test_an_unfunded_plan_is_blocked_and_reports_why(self, tmp_path, capsys):
        db = str(tmp_path / "cli.db")
        # A pot, but nothing in it: the plan is legal and unaffordable, which is
        # the case this test is about. A wallet with no pot at all could not
        # carry the plan in the first place.
        wallet_id = opened_wallet_with_pot(db, capsys)
        create_salary_plan(db, wallet_id)

        assert run(db, "plan", "tick", "--as-of", "2026-01-01") == 0
        out = capsys.readouterr().out
        assert "blocked" in out
        assert "insufficient_balance" in out

    def test_a_blocked_plan_is_paused_and_the_reason_is_kept(
        self, tmp_path, capsys
    ):
        """The row that answers "why did my plan stop?".

        A blocked run writes no transactions, so without this history the plan
        would simply have gone quiet with nothing anywhere to explain it.
        """
        db = str(tmp_path / "cli.db")
        wallet_id = opened_wallet_with_pot(db, capsys)
        create_salary_plan(db, wallet_id)
        run(db, "plan", "tick", "--as-of", "2026-01-01")
        capsys.readouterr()
        plan_id = plan_id_from(db, wallet_id, capsys)
        capsys.readouterr()

        assert run(db, "plan", "show", plan_id) == 0
        out = capsys.readouterr().out
        assert "status: paused" in out
        assert "blocked (insufficient_balance)" in out

    def test_a_tick_clears_one_occurrence_per_plan(self, tmp_path, capsys):
        """Four months behind: the tick pays the first and stops.

        Catching up one occurrence at a time bounds the burst after an outage,
        so a wallet that was down for a quarter does not pay out a quarter's
        worth in a single command.
        """
        db = str(tmp_path / "cli.db")
        wallet_id = funded_locked_wallet(db, capsys, "500000")
        create_salary_plan(db, wallet_id)

        assert run(db, "plan", "tick", "--as-of", "2026-04-15") == 0
        out = capsys.readouterr().out
        assert "2026-01-01" in out
        assert "2026-02-01" not in out

        assert run(db, "balance", wallet_id) == 0
        assert "locked: 480000.00 NGN" in capsys.readouterr().out

    def test_a_forgotten_plan_catches_up_and_retires(self, tmp_path, capsys):
        """A 3-month term starting 1 January is four runs, not three.

        ``ends_on`` is inclusive: the plan completes when the *next* occurrence
        would fall past it. So 1 Jan + 3 months = 1 Apr is still a run, and the
        plan retires after it - which is also why the assertion below is about
        the counter rather than about a particular payment being skipped.
        """
        db = str(tmp_path / "cli.db")
        wallet_id = funded_locked_wallet(db, capsys)
        create_salary_plan(db, wallet_id, "2026-01-01", "--for", "3", "months")
        plan_id = plan_id_from(db, wallet_id, capsys)
        capsys.readouterr()

        for month in ("2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01"):
            assert run(db, "plan", "tick", "--as-of", month) == 0
            capsys.readouterr()

        assert run(db, "plan", "show", plan_id) == 0
        out = capsys.readouterr().out
        assert "status: completed" in out
        assert "runs completed: 4" in out


class TestTheWarningBeforeAPayout:
    """Thirty minutes ahead, said once, and never in the way of the money.

    The headline case for the whole feature, exercised through the CLI because
    that is where the warning has to be readable. A noon plan is the only kind
    that can tell these moments apart - every other test in this file uses a
    midnight ``--as-of`` equal to a midnight anchor, where the plan is *due*
    rather than *upcoming* and no warning is ever owed.
    """

    NOON = "2026-03-02T12:00"

    def test_a_plan_half_an_hour_away_is_announced(self, tmp_path, capsys):
        db = str(tmp_path / "cli.db")
        wallet_id = funded_locked_wallet(db, capsys)
        create_salary_plan(db, wallet_id, self.NOON)
        plan_id = plan_id_from(db, wallet_id, capsys)
        capsys.readouterr()

        assert run(db, "plan", "tick", "--as-of", "2026-03-02T11:30") == 0
        out = capsys.readouterr().out

        assert f"warning: {plan_id}" in out
        assert "2026-03-02T12:00" in out
        assert "'Salary 2026'" in out
        assert "pays 20000.00 NGN" in out
        assert "in 30 minutes" in out

    def test_the_warning_moves_no_money(self, tmp_path, capsys):
        """A courtesy that spent something would not be a courtesy."""
        db = str(tmp_path / "cli.db")
        wallet_id = funded_locked_wallet(db, capsys)
        create_salary_plan(db, wallet_id, self.NOON)
        capsys.readouterr()

        assert run(db, "plan", "tick", "--as-of", "2026-03-02T11:30") == 0
        # The warning went out and the run did not: those two lines are the
        # whole of the output, and the second is the one that matters here.
        assert "nothing due" in capsys.readouterr().out

        assert run(db, "balance", wallet_id) == 0
        assert "locked: 100000.00 NGN" in capsys.readouterr().out

    def test_the_warning_is_not_repeated_on_the_next_tick(self, tmp_path, capsys):
        """Cron fires every few minutes; the user is told once."""
        db = str(tmp_path / "cli.db")
        wallet_id = funded_locked_wallet(db, capsys)
        create_salary_plan(db, wallet_id, self.NOON)
        capsys.readouterr()
        run(db, "plan", "tick", "--as-of", "2026-03-02T11:30")
        capsys.readouterr()

        assert run(db, "plan", "tick", "--as-of", "2026-03-02T11:35") == 0
        out = capsys.readouterr().out

        assert "warning:" not in out
        assert "nothing due" in out

    def test_the_run_happens_at_its_moment_with_no_warning(self, tmp_path, capsys):
        """The lower bound of the window is strict, and this is where it shows.

        At exactly noon the plan is due, so the tick pays and says nothing about
        what is coming - there is no longer anything coming. A window written
        with an inclusive lower bound would print a warning here instead.
        """
        db = str(tmp_path / "cli.db")
        wallet_id = funded_locked_wallet(db, capsys)
        create_salary_plan(db, wallet_id, self.NOON)
        capsys.readouterr()

        assert run(db, "plan", "tick", "--as-of", self.NOON) == 0
        out = capsys.readouterr().out

        assert "warning:" not in out
        assert "2026-03-02T12:00" in out
        assert "succeeded" in out

        assert run(db, "balance", wallet_id) == 0
        assert "locked: 80000.00 NGN" in capsys.readouterr().out

    def test_a_warning_does_not_stop_the_payout(self, tmp_path, capsys):
        """Warn at 11:30, pay at noon, in two ordinary ticks."""
        db = str(tmp_path / "cli.db")
        wallet_id = funded_locked_wallet(db, capsys)
        create_salary_plan(db, wallet_id, self.NOON)
        capsys.readouterr()

        run(db, "plan", "tick", "--as-of", "2026-03-02T11:30")
        capsys.readouterr()
        assert run(db, "plan", "tick", "--as-of", self.NOON) == 0
        assert "succeeded" in capsys.readouterr().out

    def test_a_missed_window_still_pays(self, tmp_path, capsys):
        """The process was down for the whole window. The money moves anyway.

        The case that makes the warning a courtesy rather than a gate: no tick
        ever ran inside the window, and the payout is unaffected.
        """
        db = str(tmp_path / "cli.db")
        wallet_id = funded_locked_wallet(db, capsys)
        create_salary_plan(db, wallet_id, self.NOON)
        capsys.readouterr()

        assert run(db, "plan", "tick", "--as-of", self.NOON) == 0
        out = capsys.readouterr().out

        assert "warning:" not in out
        assert "succeeded" in out

    def test_an_unaffordable_plan_is_still_announced(self, tmp_path, capsys):
        """Affordability is the run's business, not the warning's.

        Nothing is locked here, so noon will block - and 11:30 still warns. If
        the warning consulted the balance it would go quiet exactly when the user
        most needs to hear that a payment is about to fail.

        "Nothing is locked" means an *empty* pot, not no pot: the plan has to
        name one to exist at all, and the wallet is where that pot lives.
        """
        db = str(tmp_path / "cli.db")
        wallet_id = opened_wallet_with_pot(db, capsys)
        create_salary_plan(db, wallet_id, self.NOON)
        capsys.readouterr()

        assert run(db, "plan", "tick", "--as-of", "2026-03-02T11:30") == 0
        assert "warning:" in capsys.readouterr().out

        assert run(db, "plan", "tick", "--as-of", self.NOON) == 0
        assert "insufficient_balance" in capsys.readouterr().out

    def test_a_plan_a_month_away_is_not_announced(self, tmp_path, capsys):
        db = str(tmp_path / "cli.db")
        wallet_id = funded_locked_wallet(db, capsys)
        create_salary_plan(db, wallet_id, self.NOON)
        capsys.readouterr()

        assert run(db, "plan", "tick", "--as-of", "2026-02-02T12:00") == 0
        out = capsys.readouterr().out

        assert "warning:" not in out
        assert "nothing due" in out


class TestTheWarningIsDelivered:
    """The warning gets somewhere a person will actually see it.

    Everything above tests that the warning is *raised*; these test that it
    *arrives*. The distinction is the whole of this phase: for four phases the
    warning was written to ``tick.log`` by cron, and a courtesy nobody reads is
    not a courtesy.
    """

    NOON = "2026-03-02T12:00"

    def test_an_unconfigured_install_still_warns_and_exits_zero(
        self, tmp_path, capsys
    ):
        """No mail account is a normal install, not a broken one.

        The environment is cleared for every test by the suite's
        ``no_notification_environment`` fixture, which is what makes this
        assertion independent of how the machine running it is configured.
        """
        db = str(tmp_path / "cli.db")
        wallet_id = funded_locked_wallet(db, capsys)
        create_salary_plan(db, wallet_id, self.NOON)
        plan_id = plan_id_from(db, wallet_id, capsys)
        capsys.readouterr()

        assert run(db, "plan", "tick", "--as-of", "2026-03-02T11:30") == 0
        out = capsys.readouterr().out

        assert f"warning: {plan_id}" in out

    def test_an_unconfigured_install_says_the_warning_went_nowhere(
        self, tmp_path, capsys
    ):
        """A warning in a log file is not a delivered warning, and saying so is the
        difference between a feature the user has not set up and one they think
        is broken.

        Note this is a ``note`` and not a ``queued``: with no address there is no
        message at all, rather than one waiting for a channel. Nothing was
        queued, and the output should not imply it was.
        """
        db = str(tmp_path / "cli.db")
        wallet_id = funded_locked_wallet(db, capsys)
        create_salary_plan(db, wallet_id, self.NOON)
        capsys.readouterr()

        assert run(db, "plan", "tick", "--as-of", "2026-03-02T11:30") == 0
        out = capsys.readouterr().out

        assert "only in this log" in out
        assert "SMTP_HOST is not set" in out
        assert "queued" not in out

    def test_the_note_names_the_variable_that_is_missing(
        self, tmp_path, capsys, monkeypatch
    ):
        """Half a configuration is the case worth naming.

        Someone who has set ``SMTP_HOST`` but not the address has made a typo
        rather than a decision, and a bare "not configured" would send them to
        read the source. The line has to say which one to go and set.
        """
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        db = str(tmp_path / "cli.db")
        wallet_id = funded_locked_wallet(db, capsys)
        create_salary_plan(db, wallet_id, self.NOON)
        capsys.readouterr()

        assert run(db, "plan", "tick", "--as-of", "2026-03-02T11:30") == 0

        assert "BUDGET_NOTIFY_TO is not set" in capsys.readouterr().out

    def test_a_configured_install_emails_the_warning(
        self, tmp_path, capsys, monkeypatch, build_channel
    ):
        """The headline case, end to end through the CLI, with no socket opened.

        The deliverer is the one piece the CLI builds from the environment, so
        swapping the builder is how a recording channel gets wired in without
        the CLI growing an injection point it does not need in production. The
        use case underneath is real; only the wire is fake.
        """
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_USER", "me@example.com")
        monkeypatch.setenv("SMTP_PASSWORD", "hunter2")
        monkeypatch.setenv("BUDGET_NOTIFY_TO", "chinedu@example.com")
        channel = build_channel()
        install_deliverer(monkeypatch, channel)
        db = str(tmp_path / "cli.db")
        wallet_id = funded_locked_wallet(db, capsys)
        create_salary_plan(db, wallet_id, self.NOON)
        plan_id = plan_id_from(db, wallet_id, capsys)
        capsys.readouterr()

        # Warned and delivered in the same tick: the message is queued by the
        # notifier and the drain at the end of the pass picks it straight up.
        assert run(db, "plan", "tick", "--as-of", "2026-03-02T11:30") == 0
        out = capsys.readouterr().out

        assert f"emailed {plan_id}" in out
        assert "to chinedu@example.com" in out
        assert len(channel.sent) == 1
        assert "20000.00 NGN" in channel.sent[0].subject

    def test_a_warning_is_emailed_once_not_on_every_tick(
        self, tmp_path, capsys, monkeypatch, build_channel
    ):
        """The property that keeps a five-minute cron from sending twelve emails."""
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_USER", "me@example.com")
        monkeypatch.setenv("BUDGET_NOTIFY_TO", "chinedu@example.com")
        channel = build_channel()
        install_deliverer(monkeypatch, channel)
        db = str(tmp_path / "cli.db")
        wallet_id = funded_locked_wallet(db, capsys)
        create_salary_plan(db, wallet_id, self.NOON)
        capsys.readouterr()

        run(db, "plan", "tick", "--as-of", "2026-03-02T11:30")
        # The first tick's output, which does say "emailed", read and discarded
        # so that the assertion below is about the second tick alone.
        capsys.readouterr()

        run(db, "plan", "tick", "--as-of", "2026-03-02T11:35")
        out = capsys.readouterr().out

        assert "emailed" not in out
        assert len(channel.sent) == 1

    def test_a_dead_mail_server_does_not_fail_the_tick(
        self, tmp_path, capsys, monkeypatch, build_channel
    ):
        """The guarantee the whole error path exists for.

        ``plan tick`` runs from cron, and a cron job that exits non-zero is a
        cron job that mails its own error report - about a warning. The message
        stays queued, the line says so, and the exit code says nothing went
        wrong with the thing the tick is actually for.
        """
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_USER", "me@example.com")
        monkeypatch.setenv("BUDGET_NOTIFY_TO", "chinedu@example.com")
        install_deliverer(
            monkeypatch, build_channel(failures=[OSError("refused")])
        )
        db = str(tmp_path / "cli.db")
        wallet_id = funded_locked_wallet(db, capsys)
        create_salary_plan(db, wallet_id, self.NOON)
        capsys.readouterr()

        assert run(db, "plan", "tick", "--as-of", "2026-03-02T11:30") == 0
        out = capsys.readouterr().out

        assert "failed" in out
        assert "OSError: refused" in out
        assert "(will retry)" in out

    def test_a_warning_whose_moment_has_passed_is_not_emailed(
        self, tmp_path, capsys, monkeypatch, build_channel
    ):
        """How a stuck warning stops being stuck, and why no retry cap is needed.

        The first tick's send is refused, so the message stays queued - that is
        the retry design working. But by noon the payout has happened, and
        delivering "your payout is in 30 minutes" afterwards would be worse than
        silence. So the queue empties itself by expiring, not by sending, and the
        expiry never touches the wire.
        """
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_USER", "me@example.com")
        monkeypatch.setenv("BUDGET_NOTIFY_TO", "chinedu@example.com")
        channel = build_channel(failures=[OSError("refused")])
        install_deliverer(monkeypatch, channel)
        db = str(tmp_path / "cli.db")
        wallet_id = funded_locked_wallet(db, capsys)
        create_salary_plan(db, wallet_id, self.NOON)
        plan_id = plan_id_from(db, wallet_id, capsys)
        # Queued and refused, so the warning is still owed. Output discarded.
        assert run(db, "plan", "tick", "--as-of", "2026-03-02T11:30") == 0
        capsys.readouterr()

        # This tick is past noon: too late to be useful, and it pays the plan.
        assert run(db, "plan", "tick", "--as-of", self.NOON) == 0
        out = capsys.readouterr().out

        assert f"expired {plan_id}" in out
        assert channel.sent == []  # never delivered
        assert len(channel.attempts) == 1  # the expiry did not try again

    def test_a_refused_warning_is_emailed_on_a_later_tick(
        self, tmp_path, capsys, monkeypatch, build_channel
    ):
        """The other side of the same coin: still in time, so it goes out.

        A mail server being down for one tick is not a lost warning - it is the
        same warning, five minutes later. This is the case the whole outbox
        exists for, and it is only visible because the message outlives the tick
        that composed it.
        """
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_USER", "me@example.com")
        monkeypatch.setenv("BUDGET_NOTIFY_TO", "chinedu@example.com")
        # One failure queued, so the second attempt gets through.
        channel = build_channel(failures=[OSError("refused")])
        install_deliverer(monkeypatch, channel)
        db = str(tmp_path / "cli.db")
        wallet_id = funded_locked_wallet(db, capsys)
        create_salary_plan(db, wallet_id, self.NOON)
        plan_id = plan_id_from(db, wallet_id, capsys)
        assert run(db, "plan", "tick", "--as-of", "2026-03-02T11:30") == 0
        capsys.readouterr()

        assert run(db, "plan", "tick", "--as-of", "2026-03-02T11:40") == 0
        out = capsys.readouterr().out

        assert f"emailed {plan_id}" in out
        assert len(channel.sent) == 1

    def test_a_warning_left_over_from_a_removed_configuration_is_queued(
        self, tmp_path, capsys, monkeypatch, build_channel
    ):
        """The one state the word "queued" is actually for, and it is not the same
        as a fresh install.

        A message can outlive the configuration that created it: it was queued
        while email worked, the send failed, and the mail account has since gone
        away. It is still owed, and now it is waiting for a channel rather than
        waiting for a retry. A fresh install never reaches this - with no address
        it composes no message at all - which is why the two states get two
        different lines.
        """
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_USER", "me@example.com")
        monkeypatch.setenv("BUDGET_NOTIFY_TO", "chinedu@example.com")
        install_deliverer(monkeypatch, build_channel(failures=[OSError("refused")]))
        db = str(tmp_path / "cli.db")
        wallet_id = funded_locked_wallet(db, capsys)
        create_salary_plan(db, wallet_id, self.NOON)
        plan_id = plan_id_from(db, wallet_id, capsys)
        assert run(db, "plan", "tick", "--as-of", "2026-03-02T11:30") == 0
        capsys.readouterr()

        # The install is no longer configured to send anything at all.
        monkeypatch.delenv("SMTP_HOST")
        install_deliverer(monkeypatch, None)

        assert run(db, "plan", "tick", "--as-of", "2026-03-02T11:35") == 0
        out = capsys.readouterr().out

        assert f"queued  {plan_id}" in out
        assert "SMTP_HOST is not set" in out

    def test_an_empty_queue_adds_nothing_to_the_output(self, tmp_path, capsys):
        """Most ticks have no mail to send, and the log must not fill up saying so."""
        db = str(tmp_path / "cli.db")
        wallet_id = funded_locked_wallet(db, capsys)
        create_salary_plan(db, wallet_id, self.NOON)
        capsys.readouterr()

        assert run(db, "plan", "tick", "--as-of", "2026-02-02T12:00") == 0
        out = capsys.readouterr().out

        assert "emailed" not in out
        assert "queued" not in out
        assert "failed" not in out
        assert "expired" not in out
        # And no note either: there was no warning to explain the fate of.
        assert "note:" not in out


class TestTheReceiptAfterThePayout:
    """The other half of the pair, and the one the user actually waits for.

    Everything above is about a message *before* the money moves. These are about
    the message *after*, and the difference is not cosmetic: the warning is a
    courtesy that a missed tick can forfeit entirely, while the receipt is the
    record of something that happened. There is no window it can miss and no
    tick that can be too late for it.
    """

    NOON = "2026-03-02T12:00"

    def test_a_payout_emails_its_receipt_in_the_same_tick(
        self, tmp_path, capsys, monkeypatch, build_channel
    ):
        """The headline of this phase: at noon the run, and the mail, happen.

        Note the two are one invocation but not one transaction. The run commits
        its own receipt *inside* the transaction that moves the money (see
        ``ExecutePlanRun``); this drain is what puts it on the wire afterwards, and
        it runs last in ``_plan_tick`` for exactly this reason - a receipt drained
        before the runs would describe nothing.
        """
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_USER", "me@example.com")
        monkeypatch.setenv("BUDGET_NOTIFY_TO", "chinedu@example.com")
        db = str(tmp_path / "cli.db")
        # Funded first, so that the deposit's own receipt goes to the default
        # channel and what this test counts is the payout alone.
        wallet_id = funded_locked_wallet(db, capsys)
        create_salary_plan(db, wallet_id)
        receipts = build_channel()
        install_notification_deliverer(monkeypatch, receipts)
        capsys.readouterr()

        assert run(db, "plan", "tick", "--as-of", self.NOON) == 0
        out = capsys.readouterr().out

        # The line about the money is still there, and unchanged: the receipt is
        # added to the tick's output, never instead of it.
        assert "succeeded" in out
        assert "emailed payout_succeeded" in out
        assert "to chinedu@example.com" in out

        assert len(receipts.sent) == 1
        assert receipts.sent[0].kind.value == "payout_succeeded"
        assert "20000.00 NGN" in receipts.sent[0].subject

    def test_the_receipt_is_one_message_for_the_whole_run(
        self, tmp_path, capsys, monkeypatch, build_channel
    ):
        """A payroll to three accounts is one email, not three.

        The decision is about the reader, not the mailbox: three messages arriving
        together would have to be reassembled by hand to answer "did the run
        happen?". The instructions are listed in the body instead, which is the
        same information in the order it was executed.
        """
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_USER", "me@example.com")
        monkeypatch.setenv("BUDGET_NOTIFY_TO", "chinedu@example.com")
        db = str(tmp_path / "cli.db")
        wallet_id = funded_locked_wallet(db, capsys, "500000")
        run(
            db, "plan", "create", "--wallet", wallet_id, "--name", "split",
            "--source", "locked", "--from-fund", "Savings",
            "--every", "monthly", "--from", "2026-01-01",
            "--pay", "20000", "0123456789", "058", "Chinedu Okafor", "salary",
            "--pay", "15000", "0987654321", "058", "Ada Nwosu", "rent",
        )
        receipts = build_channel()
        install_notification_deliverer(monkeypatch, receipts)
        capsys.readouterr()

        assert run(db, "plan", "tick", "--as-of", "2026-01-01") == 0

        assert len(receipts.sent) == 1
        body = receipts.sent[0].body
        assert "Chinedu Okafor" in body
        assert "Ada Nwosu" in body

    def test_a_blocked_run_emails_why_it_could_not_pay(
        self, tmp_path, capsys, monkeypatch, build_channel
    ):
        """The message a user most needs and least expects.

        Nothing moved, so nothing else will tell them: the wallet is unchanged,
        there is no ledger row, and the plan has simply gone quiet. Without this
        the only trace is a ``plan_runs`` row nobody reads until the plan is
        already known to be stuck.
        """
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_USER", "me@example.com")
        monkeypatch.setenv("BUDGET_NOTIFY_TO", "chinedu@example.com")
        db = str(tmp_path / "cli.db")
        wallet_id = opened_wallet_with_pot(db, capsys)
        create_salary_plan(db, wallet_id)
        receipts = build_channel()
        install_notification_deliverer(monkeypatch, receipts)
        capsys.readouterr()

        assert run(db, "plan", "tick", "--as-of", "2026-01-01") == 0
        out = capsys.readouterr().out

        assert "insufficient_balance" in out
        assert "emailed payout_blocked" in out
        assert len(receipts.sent) == 1
        # The sentence that decides what the user does next: paused *and* still
        # owing the occurrence, so topping the wallet up and resuming pays it.
        assert "paused" in receipts.sent[0].body

    def test_a_wallet_command_emails_its_own_receipt_before_it_exits(
        self, tmp_path, capsys, monkeypatch, build_channel
    ):
        """"Immediately" is a claim about which invocation does the sending.

        Left to ``plan tick``, a deposit made at 14:03 would be reported whenever
        the scheduler next ran - and an install with no scheduler running would
        never report it at all. So the command that moved the money delivers it.
        """
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_USER", "me@example.com")
        monkeypatch.setenv("BUDGET_NOTIFY_TO", "chinedu@example.com")
        receipts = build_channel()
        install_notification_deliverer(monkeypatch, receipts)
        db = str(tmp_path / "cli.db")
        wallet_id = opened_wallet_id(db, capsys)
        capsys.readouterr()

        assert run(db, "deposit", wallet_id, "2500.50") == 0
        out = capsys.readouterr().out

        assert "deposited 2500.50 NGN" in out
        assert "emailed wallet_deposit" in out
        assert len(receipts.sent) == 1
        assert "2500.50 NGN" in receipts.sent[0].subject

    def test_a_repeated_deposit_is_emailed_once(
        self, tmp_path, capsys, monkeypatch, build_channel
    ):
        """The idempotent path, which is where the derived key earns its keep.

        Repeating an operation with the same ``--ref`` returns the *existing*
        ledger row rather than moving money twice - so the receipt is composed a
        second time, from the same row, with the same event key. The insert is
        what refuses it, not a check: the second enqueue inserts nothing, so there
        is no second email to suppress.
        """
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_USER", "me@example.com")
        monkeypatch.setenv("BUDGET_NOTIFY_TO", "chinedu@example.com")
        receipts = build_channel()
        install_notification_deliverer(monkeypatch, receipts)
        db = str(tmp_path / "cli.db")
        wallet_id = opened_wallet_id(db, capsys)
        run(db, "deposit", wallet_id, "500", "--ref", "one-off")
        capsys.readouterr()

        assert run(db, "deposit", wallet_id, "500", "--ref", "one-off") == 0
        out = capsys.readouterr().out

        assert "emailed" not in out
        assert len(receipts.sent) == 1

    def test_a_dead_mail_server_does_not_fail_a_wallet_command(
        self, tmp_path, capsys, monkeypatch, build_channel
    ):
        """The money is committed before the socket opens, and that is the point.

        A deposit that *reported* failure because the mail server was unreachable
        would be wrong twice over: the money moved, and the receipt is still owed.
        The command exits zero, the balance it printed is durable, and the message
        stays queued for the next drain.
        """
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_USER", "me@example.com")
        monkeypatch.setenv("BUDGET_NOTIFY_TO", "chinedu@example.com")
        install_notification_deliverer(
            monkeypatch, build_channel(failures=[OSError("refused")])
        )
        db = str(tmp_path / "cli.db")
        wallet_id = opened_wallet_id(db, capsys)
        capsys.readouterr()

        assert run(db, "deposit", wallet_id, "500") == 0
        out = capsys.readouterr().out

        assert "deposited 500.00 NGN" in out
        assert "failed  wallet_deposit" in out
        assert "OSError: refused (will retry)" in out

        assert run(db, "balance", wallet_id) == 0
        assert "available: 500.00 NGN" in capsys.readouterr().out

    def test_an_unconfigured_install_reports_nothing_extra(
        self, tmp_path, capsys
    ):
        """No mail account is a normal install, and its commands stay quiet.

        The environment is cleared for every test by conftest, which is what makes
        this assertion independent of the machine running it. With no recipient
        nothing is composed at all - so there is no message to defer, and no line
        about one.
        """
        db = str(tmp_path / "cli.db")
        wallet_id = opened_wallet_id(db, capsys)
        capsys.readouterr()

        assert run(db, "deposit", wallet_id, "500") == 0
        out = capsys.readouterr().out

        assert "deposited 500.00 NGN" in out
        assert "emailed" not in out
        assert "queued" not in out
        assert "failed" not in out


class TestSteering:
    def test_pause_and_resume_round_trip(self, tmp_path, capsys):
        db = str(tmp_path / "cli.db")
        wallet_id = funded_locked_wallet(db, capsys)
        create_salary_plan(db, wallet_id)
        plan_id = plan_id_from(db, wallet_id, capsys)
        capsys.readouterr()

        assert run(db, "plan", "pause", plan_id) == 0
        assert "is now paused" in capsys.readouterr().out
        assert run(db, "plan", "resume", plan_id) == 0
        assert "is now active" in capsys.readouterr().out

    def test_a_paused_plan_is_not_ticked(self, tmp_path, capsys):
        db = str(tmp_path / "cli.db")
        wallet_id = funded_locked_wallet(db, capsys)
        create_salary_plan(db, wallet_id)
        plan_id = plan_id_from(db, wallet_id, capsys)
        capsys.readouterr()
        run(db, "plan", "pause", plan_id)
        capsys.readouterr()

        assert run(db, "plan", "tick", "--as-of", "2026-01-01") == 0
        assert "nothing due" in capsys.readouterr().out

    def test_cancel_ends_the_plan(self, tmp_path, capsys):
        db = str(tmp_path / "cli.db")
        wallet_id = funded_locked_wallet(db, capsys)
        create_salary_plan(db, wallet_id)
        plan_id = plan_id_from(db, wallet_id, capsys)
        capsys.readouterr()

        assert run(db, "plan", "cancel", plan_id) == 0
        assert "is now cancelled" in capsys.readouterr().out

    def test_a_release_plan_refuses_to_be_cancelled(self, tmp_path, capsys):
        """The lock is rigid; this is where the user finds that out.

        Exit 1 with the domain's own words, because the rule has one
        implementation and it is not here.
        """
        db = str(tmp_path / "cli.db")
        wallet_id = funded_locked_wallet(db, capsys)
        run(db, "plan", "create", "--wallet", wallet_id, "--name", "Unlock",
            "--source", "locked", "--from-fund", "Savings",
            "--every", "monthly", "--from", "2026-01-01",
            "--for", "6", "months", "--release", "5000", "emergency")
        capsys.readouterr()
        plan_id = plan_id_from(db, wallet_id, capsys)
        capsys.readouterr()

        assert run(db, "plan", "cancel", plan_id) == 1
        assert "error:" in capsys.readouterr().err

    def test_a_release_plan_is_marked_in_the_list(self, tmp_path, capsys):
        db = str(tmp_path / "cli.db")
        wallet_id = funded_locked_wallet(db, capsys)
        run(db, "plan", "create", "--wallet", wallet_id, "--name", "Unlock",
            "--source", "locked", "--from-fund", "Savings",
            "--every", "monthly", "--from", "2026-01-01",
            "--for", "6", "months", "--release", "5000", "emergency")
        capsys.readouterr()

        assert run(db, "plan", "list", wallet_id) == 0
        assert "(irreversible)" in capsys.readouterr().out

    def test_an_open_ended_release_plan_is_refused(self, tmp_path, capsys):
        """Irreversible *and* endless would be locked money with no exit at all.

        The rule is derived rather than chosen: "irreversible until the set date"
        only means anything if there is a set date.
        """
        db = str(tmp_path / "cli.db")
        wallet_id = funded_locked_wallet(db, capsys)

        assert run(db, "plan", "create", "--wallet", wallet_id, "--name", "Unlock",
                   "--source", "locked", "--from-fund", "Savings",
                   "--every", "monthly", "--from",
                   "2026-01-01", "--release", "5000", "emergency") == 1
        assert "error:" in capsys.readouterr().err

    def test_steering_an_unknown_plan_is_an_error(self, tmp_path, capsys):
        db = str(tmp_path / "cli.db")

        assert run(db, "plan", "cancel", str(uuid4())) == 1
        assert "error:" in capsys.readouterr().err


class TestEditing:
    def test_edit_replaces_the_lines(self, tmp_path, capsys):
        db = str(tmp_path / "cli.db")
        wallet_id = funded_locked_wallet(db, capsys)
        create_salary_plan(db, wallet_id)
        plan_id = plan_id_from(db, wallet_id, capsys)
        capsys.readouterr()

        assert run(db, "plan", "edit", plan_id, *salary_lines("25000")) == 0
        capsys.readouterr()

        assert run(db, "plan", "list", wallet_id) == 0
        assert "25000.00 NGN" in capsys.readouterr().out

    def test_edit_reads_amounts_in_the_plans_currency(self, tmp_path, capsys):
        db = str(tmp_path / "cli.db")
        wallet_id = opened_wallet_id(db, capsys, currency="USD")
        run(db, "plan", "create", "--wallet", wallet_id, "--name", "USD plan",
            "--source", "available", "--every", "monthly", "--from", "2026-01-01",
            *salary_lines())
        capsys.readouterr()
        plan_id = plan_id_from(db, wallet_id, capsys)
        capsys.readouterr()

        assert run(db, "plan", "edit", plan_id, *salary_lines("30000")) == 0
        capsys.readouterr()

        assert run(db, "plan", "list", wallet_id) == 0
        assert "30000.00 USD" in capsys.readouterr().out

    def test_an_edit_with_no_lines_is_refused(self, tmp_path, capsys):
        db = str(tmp_path / "cli.db")
        wallet_id = funded_locked_wallet(db, capsys)
        create_salary_plan(db, wallet_id)
        plan_id = plan_id_from(db, wallet_id, capsys)
        capsys.readouterr()

        assert run(db, "plan", "edit", plan_id) == 1
        assert "error:" in capsys.readouterr().err

    def test_a_refused_edit_does_not_change_the_plan(self, tmp_path, capsys):
        db = str(tmp_path / "cli.db")
        wallet_id = funded_locked_wallet(db, capsys)
        create_salary_plan(db, wallet_id)
        plan_id = plan_id_from(db, wallet_id, capsys)
        capsys.readouterr()
        run(db, "plan", "edit", plan_id)
        capsys.readouterr()

        assert run(db, "plan", "list", wallet_id) == 0
        assert "20000.00 NGN" in capsys.readouterr().out


class TestUsage:
    def test_a_plan_with_no_lines_is_refused(self, tmp_path, capsys):
        db = str(tmp_path / "cli.db")
        wallet_id = funded_locked_wallet(db, capsys)

        assert run(db, "plan", "create", "--wallet", wallet_id, "--name", "empty",
                   "--source", "locked", "--from-fund", "Savings",
                   "--every", "monthly",
                   "--from", "2026-01-01") == 1
        assert "error:" in capsys.readouterr().err

    def test_an_unknown_source_word_is_a_usage_error(self, tmp_path):
        db = str(tmp_path / "cli.db")

        with pytest.raises(SystemExit) as excinfo:
            run(db, "plan", "create", "--wallet", str(uuid4()), "--name", "x",
                "--source", "pocket", "--every", "monthly", *salary_lines())
        assert excinfo.value.code == 2

    def test_an_unknown_cadence_word_is_a_usage_error(self, tmp_path):
        db = str(tmp_path / "cli.db")

        with pytest.raises(SystemExit) as excinfo:
            run(db, "plan", "create", "--wallet", str(uuid4()), "--name", "x",
                "--source", "locked", "--every", "fortnightly", *salary_lines())
        assert excinfo.value.code == 2

    def test_an_incomplete_pay_line_is_a_usage_error(self, tmp_path):
        """Five fields, and argparse counts them before anything else runs."""
        db = str(tmp_path / "cli.db")

        with pytest.raises(SystemExit) as excinfo:
            run(db, "plan", "create", "--wallet", str(uuid4()), "--name", "x",
                "--source", "locked", "--every", "monthly",
                "--pay", "20000", "0123456789")
        assert excinfo.value.code == 2

    def test_a_non_numeric_amount_is_a_domain_error(self, tmp_path, capsys):
        db = str(tmp_path / "cli.db")
        wallet_id = funded_locked_wallet(db, capsys)

        assert create_salary_plan(db, wallet_id, "2026-01-01") == 0
        capsys.readouterr()
        plan_id = plan_id_from(db, wallet_id, capsys)
        capsys.readouterr()

        assert run(db, "plan", "edit", plan_id,
                   "--pay", "abc", "0123456789", "058", "X", "salary") == 1
        assert "error: invalid amount" in capsys.readouterr().err

    def test_plan_with_no_subcommand_is_a_usage_error(self, tmp_path):
        db = str(tmp_path / "cli.db")

        with pytest.raises(SystemExit) as excinfo:
            run(db, "plan")
        assert excinfo.value.code == 2

    def test_a_plan_for_an_unknown_wallet_is_an_error(self, tmp_path, capsys):
        db = str(tmp_path / "cli.db")

        assert create_salary_plan(db, str(uuid4())) == 1
        assert "error:" in capsys.readouterr().err
