from datetime import date, datetime
from decimal import Decimal

import pytest

from app.application.planning.execute_plan_run import ExecutePlanRun
from app.domain.money.currency import Currency
from app.domain.money.destination import Destination
from app.domain.money.destinationKind import DestinationKind
from app.domain.money.fundKind import FundKind
from app.domain.money.money import Money
from app.domain.money.transactionType import TransactionType
from app.domain.money.walletStatus import WalletStatus
from app.domain.notifications.notificationKind import NotificationKind
from app.domain.planning.instruction import Instruction
from app.domain.planning.plannedAction import PlannedAction
from app.domain.planning.planSource import PlanSource
from app.domain.planning.planStatus import PlanStatus
from app.domain.planning.runBlockReason import RunBlockReason
from app.domain.planning.runStatus import RunStatus
from app.infrastructure.persistence.sqlite_unit_of_work import SqliteUnitOfWorkFactory

NGN = Currency.NGN

#: Where a run's receipt is addressed. Passed in rather than read from the
#: environment, because ``ExecutePlanRun`` takes its inputs as arguments -
#: reading ``os.environ`` is the CLI's job, not the use case's.
RECIPIENT = "chinedu@example.com"

BANK = Destination(
    kind=DestinationKind.BANK_ACCOUNT,
    identifier="0123456789",
    name="Chinedu Okafor",
    details={"bank_code": "058"},
)


def payout(amount: str, label: str = "salary") -> Instruction:
    return Instruction(
        action=PlannedAction.PAYOUT,
        amount=Money(Decimal(amount), NGN),
        label=label,
        destination=BANK,
    )


def release(amount: str, label: str = "emergency") -> Instruction:
    return Instruction(
        action=PlannedAction.RELEASE,
        amount=Money(Decimal(amount), NGN),
        label=label,
    )


# --- harness ---------------------------------------------------------------


def build_executor(tmp_path, name="plans.db", recipient=None):
    factory = SqliteUnitOfWorkFactory(str(tmp_path / name))
    return ExecutePlanRun(factory, recipient=recipient), factory


def seed(factory, wallet, plan):
    """Persist a wallet and its plan, the way the app would have created them."""
    uow = factory.start()
    try:
        uow.wallets.save(wallet)
        uow.plans.save(plan)
        uow.commit()
    except BaseException:
        uow.rollback()
        raise


def read(factory, accessor):
    uow = factory.start()
    try:
        return accessor(uow)
    finally:
        uow.rollback()


def wallet_after(factory, wallet_id):
    return read(factory, lambda uow: uow.wallets.get_by_id(wallet_id))


def plan_after(factory, plan_id):
    return read(factory, lambda uow: uow.plans.get_by_id(plan_id))


def runs_of(factory, plan_id):
    return read(factory, lambda uow: uow.plan_runs.list_by_plan_id(plan_id))


def ledger_of(factory, wallet_id):
    return read(factory, lambda uow: uow.transactions.get_by_wallet_id(wallet_id))


def notifications_of(factory):
    """Every receipt still owed - the queue, not the report.

    ``pending`` rather than a direct row query, so a settled row is correctly
    reported as "no longer owed" and the tests below can go on asking the same
    question the drain asks.
    """
    return read(factory, lambda uow: uow.notifications.pending())


class ExplodingCommit:
    """A unit of work that writes normally and then fails to commit.

    The way to ask "do these two writes really land together?" is to break the
    commit and look at what is left. A unit that delegated everything but could
    still commit could not answer it; this one forwards every repository and
    refuses only the last step, which is exactly the failure a crash between two
    separate transactions would produce.
    """

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def commit(self):
        raise RuntimeError("commit failed")


class ExplodingCommitFactory:
    def __init__(self, inner):
        self._inner = inner

    def start(self):
        return ExplodingCommit(self._inner.start())


def top_up_locked(factory, wallet_id, amount):
    """Deposit and then lock into a pot, so the money lands where the plan spends.

    A plain deposit would not do: it credits the *available* balance, and a
    LOCKED-source plan is funded by locked money only. A test that deposited and
    expected the plan to run would be testing the fallback the design forbids.

    The pot is looked up by name rather than taken as an argument because the
    plan cannot name one either - see ``execute_plan_run``'s ``_operation_for``.
    A plan draws on the pool of matured pots, so the pot the money sits in is
    immaterial here as long as it is open.
    """
    uow = factory.start()
    try:
        wallet = uow.wallets.get_by_id(wallet_id)
        wallet.apply_deposit(Money(Decimal(amount), NGN))
        wallet.lock_into_fund(wallet.fund_by_name("Locked").fund_id, Money(Decimal(amount), NGN))
        uow.wallets.save(wallet)
        uow.commit()
    except BaseException:
        uow.rollback()
        raise


def resume(factory, plan_id):
    uow = factory.start()
    try:
        plan = uow.plans.get_by_id(plan_id)
        plan.resume()
        uow.plans.save(plan)
        uow.commit()
    except BaseException:
        uow.rollback()
        raise


ANCHOR = datetime(2026, 1, 1)


# --- the happy path --------------------------------------------------------


class TestAPlanThatCanPay:
    def test_the_instruction_moves_money_out_of_locked(
        self, build_wallet, build_plan, tmp_path
    ):
        wallet = build_wallet(available="5000", locked="10000")
        plan = build_plan(wallet_id=wallet.wallet_id, instructions=(payout("2000"),))
        executor, factory = build_executor(tmp_path)
        seed(factory, wallet, plan)

        executor.execute(plan.plan_id, ANCHOR)

        after = wallet_after(factory, wallet.wallet_id)
        assert after.locked_balance == Money(Decimal("8000"), NGN)
        assert after.available_balance == Money(Decimal("5000"), NGN)

    def test_the_run_is_recorded_as_succeeded(
        self, build_wallet, build_plan, tmp_path
    ):
        wallet = build_wallet(locked="10000")
        plan = build_plan(wallet_id=wallet.wallet_id, instructions=(payout("2000"),))
        executor, factory = build_executor(tmp_path)
        seed(factory, wallet, plan)

        run = executor.execute(plan.plan_id, ANCHOR)

        assert run.status is RunStatus.SUCCEEDED
        assert run.due_at == ANCHOR
        assert run.reason is None
        assert runs_of(factory, plan.plan_id)[0].status is RunStatus.SUCCEEDED

    def test_the_plan_advances_to_the_next_occurrence(
        self, build_wallet, build_plan, tmp_path
    ):
        wallet = build_wallet(locked="10000")
        plan = build_plan(wallet_id=wallet.wallet_id, instructions=(payout("2000"),))
        executor, factory = build_executor(tmp_path)
        seed(factory, wallet, plan)

        executor.execute(plan.plan_id, ANCHOR)

        after = plan_after(factory, plan.plan_id)
        assert after.completed_runs == 1
        assert after.next_due_at == datetime(2026, 2, 1)

    def test_one_instruction_writes_one_ledger_row(
        self, build_wallet, build_plan, tmp_path
    ):
        wallet = build_wallet(locked="10000")
        plan = build_plan(wallet_id=wallet.wallet_id, instructions=(payout("2000"),))
        executor, factory = build_executor(tmp_path)
        seed(factory, wallet, plan)

        executor.execute(plan.plan_id, ANCHOR)

        entries = ledger_of(factory, wallet.wallet_id)
        assert len(entries) == 1
        assert entries[0].type is TransactionType.PAYOUT
        assert entries[0].amount == Money(Decimal("2000"), NGN)
        assert entries[0].destination == BANK

    def test_the_headline_case_pays_five_accounts_in_one_run(
        self, build_wallet, build_plan, tmp_path
    ):
        """100k locked, 20k to each of five accounts - one run, five ledger rows."""
        wallet = build_wallet(locked="100000")
        plan = build_plan(
            wallet_id=wallet.wallet_id,
            instructions=tuple(payout("20000", f"salary {n}") for n in range(5)),
        )
        executor, factory = build_executor(tmp_path)
        seed(factory, wallet, plan)

        executor.execute(plan.plan_id, ANCHOR)

        assert wallet_after(factory, wallet.wallet_id).locked_balance == Money(
            Decimal("0"), NGN
        )
        assert len(ledger_of(factory, wallet.wallet_id)) == 5
        assert plan_after(factory, plan.plan_id).completed_runs == 1


class TestPlansThatSpendOtherBalances:
    def test_an_available_source_plan_spends_the_available_balance(
        self, build_wallet, build_plan, tmp_path
    ):
        """Scheduling is not conditional on locking - the product's other half."""
        wallet = build_wallet(available="5000", locked="9000")
        plan = build_plan(
            wallet_id=wallet.wallet_id,
            source=PlanSource.AVAILABLE,
            instructions=(payout("2000"),),
        )
        executor, factory = build_executor(tmp_path)
        seed(factory, wallet, plan)

        executor.execute(plan.plan_id, ANCHOR)

        after = wallet_after(factory, wallet.wallet_id)
        assert after.available_balance == Money(Decimal("3000"), NGN)
        assert after.locked_balance == Money(Decimal("9000"), NGN)

    def test_a_release_plan_moves_locked_to_available(
        self, build_wallet, build_plan, tmp_path
    ):
        wallet = build_wallet(available="1000", locked="5000")
        plan = build_plan(
            wallet_id=wallet.wallet_id,
            instructions=(release("1500"),),
            ends_on=date(2026, 6, 1),
        )
        executor, factory = build_executor(tmp_path)
        seed(factory, wallet, plan)

        executor.execute(plan.plan_id, ANCHOR)

        after = wallet_after(factory, wallet.wallet_id)
        assert after.locked_balance == Money(Decimal("3500"), NGN)
        assert after.available_balance == Money(Decimal("2500"), NGN)

    def test_a_release_leaves_total_holdings_unchanged(
        self, build_wallet, build_plan, tmp_path
    ):
        """Nothing left the wallet - only the reservation was lifted."""
        wallet = build_wallet(available="1000", locked="5000")
        plan = build_plan(
            wallet_id=wallet.wallet_id,
            instructions=(release("1500"),),
            ends_on=date(2026, 6, 1),
        )
        executor, factory = build_executor(tmp_path)
        seed(factory, wallet, plan)

        executor.execute(plan.plan_id, ANCHOR)

        after = wallet_after(factory, wallet.wallet_id)
        assert after.available_balance + after.locked_balance == Money(
            Decimal("6000"), NGN
        )


# --- nothing to do ---------------------------------------------------------


class TestWhenThePlanIsNotDue:
    def test_a_plan_before_its_anchor_records_nothing(
        self, build_wallet, build_plan, tmp_path
    ):
        wallet = build_wallet(locked="10000")
        plan = build_plan(wallet_id=wallet.wallet_id, instructions=(payout("2000"),))
        executor, factory = build_executor(tmp_path)
        seed(factory, wallet, plan)

        run = executor.execute(plan.plan_id, datetime(2025, 12, 31))

        assert run is None
        assert runs_of(factory, plan.plan_id) == []
        assert wallet_after(factory, wallet.wallet_id).locked_balance == Money(
            Decimal("10000"), NGN
        )

    def test_a_plan_that_already_ran_is_not_due_again(
        self, build_wallet, build_plan, tmp_path
    ):
        wallet = build_wallet(locked="10000")
        plan = build_plan(wallet_id=wallet.wallet_id, instructions=(payout("2000"),))
        executor, factory = build_executor(tmp_path)
        seed(factory, wallet, plan)

        executor.execute(plan.plan_id, ANCHOR)
        second = executor.execute(plan.plan_id, ANCHOR)

        assert second is None
        assert wallet_after(factory, wallet.wallet_id).locked_balance == Money(
            Decimal("8000"), NGN
        )

    def test_a_paused_plan_is_not_due(self, build_wallet, build_plan, tmp_path):
        wallet = build_wallet(locked="10000")
        plan = build_plan(
            wallet_id=wallet.wallet_id,
            instructions=(payout("2000"),),
            status=PlanStatus.PAUSED,
        )
        executor, factory = build_executor(tmp_path)
        seed(factory, wallet, plan)

        assert executor.execute(plan.plan_id, ANCHOR) is None


# --- refusals --------------------------------------------------------------


class TestRunsThatAreBlocked:
    def test_insufficient_funds_blocks_instead_of_paying(
        self, build_wallet, build_plan, tmp_path
    ):
        wallet = build_wallet(locked="500")
        plan = build_plan(wallet_id=wallet.wallet_id, instructions=(payout("2000"),))
        executor, factory = build_executor(tmp_path)
        seed(factory, wallet, plan)

        run = executor.execute(plan.plan_id, ANCHOR)

        assert run.status is RunStatus.BLOCKED
        assert run.reason is RunBlockReason.INSUFFICIENT_BALANCE

    def test_a_blocked_run_moves_no_money(self, build_wallet, build_plan, tmp_path):
        wallet = build_wallet(available="1000", locked="500")
        plan = build_plan(wallet_id=wallet.wallet_id, instructions=(payout("2000"),))
        executor, factory = build_executor(tmp_path)
        seed(factory, wallet, plan)

        executor.execute(plan.plan_id, ANCHOR)

        after = wallet_after(factory, wallet.wallet_id)
        assert after.locked_balance == Money(Decimal("500"), NGN)
        assert after.available_balance == Money(Decimal("1000"), NGN)

    def test_a_blocked_run_writes_no_ledger_rows_at_all(
        self, build_wallet, build_plan, tmp_path
    ):
        """Pre-flight runs before anything is attempted, so no FAILED rows either."""
        wallet = build_wallet(locked="500")
        plan = build_plan(wallet_id=wallet.wallet_id, instructions=(payout("2000"),))
        executor, factory = build_executor(tmp_path)
        seed(factory, wallet, plan)

        executor.execute(plan.plan_id, ANCHOR)

        assert ledger_of(factory, wallet.wallet_id) == []

    def test_a_blocked_run_pauses_the_plan(self, build_wallet, build_plan, tmp_path):
        wallet = build_wallet(locked="500")
        plan = build_plan(wallet_id=wallet.wallet_id, instructions=(payout("2000"),))
        executor, factory = build_executor(tmp_path)
        seed(factory, wallet, plan)

        executor.execute(plan.plan_id, ANCHOR)

        assert plan_after(factory, plan.plan_id).status is PlanStatus.PAUSED

    def test_a_blocked_run_does_not_advance_the_occurrence(
        self, build_wallet, build_plan, tmp_path
    ):
        """The plan still owes January - it did not skip it."""
        wallet = build_wallet(locked="500")
        plan = build_plan(wallet_id=wallet.wallet_id, instructions=(payout("2000"),))
        executor, factory = build_executor(tmp_path)
        seed(factory, wallet, plan)

        executor.execute(plan.plan_id, ANCHOR)

        after = plan_after(factory, plan.plan_id)
        assert after.completed_runs == 0
        assert after.next_due_at == ANCHOR

    def test_a_frozen_wallet_blocks_a_payout(self, build_wallet, build_plan, tmp_path):
        wallet = build_wallet(status=WalletStatus.FROZEN, locked="10000")
        plan = build_plan(wallet_id=wallet.wallet_id, instructions=(payout("2000"),))
        executor, factory = build_executor(tmp_path)
        seed(factory, wallet, plan)

        run = executor.execute(plan.plan_id, ANCHOR)

        assert run.reason is RunBlockReason.WALLET_FROZEN

    def test_a_closed_wallet_blocks_the_run(self, build_wallet, build_plan, tmp_path):
        wallet = build_wallet(status=WalletStatus.CLOSED, locked="10000")
        plan = build_plan(wallet_id=wallet.wallet_id, instructions=(payout("2000"),))
        executor, factory = build_executor(tmp_path)
        seed(factory, wallet, plan)

        run = executor.execute(plan.plan_id, ANCHOR)

        assert run.reason is RunBlockReason.WALLET_CLOSED

    def test_a_closed_wallet_blocks_even_a_release(
        self, build_wallet, build_plan, tmp_path
    ):
        """A closed wallet permits nothing - unlike a frozen one, which permits a reshuffle."""
        wallet = build_wallet(status=WalletStatus.CLOSED, locked="5000")
        plan = build_plan(
            wallet_id=wallet.wallet_id,
            instructions=(release("1500"),),
            ends_on=date(2026, 6, 1),
        )
        executor, factory = build_executor(tmp_path)
        seed(factory, wallet, plan)

        run = executor.execute(plan.plan_id, ANCHOR)

        assert run.reason is RunBlockReason.WALLET_CLOSED


class TestFrozenIsNotSimplyRefused:
    """The subtle case the pre-flight status check exists for.

    A frozen wallet permits a release - money does not leave, it only moves
    between balances. It refuses every payout. So "frozen" is not a blanket no,
    and a plan mixing both would half-execute if status were discovered
    instruction-by-instruction.
    """

    def test_a_frozen_wallet_still_allows_a_release_only_plan(
        self, build_wallet, build_plan, tmp_path
    ):
        wallet = build_wallet(status=WalletStatus.FROZEN, available="1000", locked="5000")
        plan = build_plan(
            wallet_id=wallet.wallet_id,
            instructions=(release("1500"),),
            ends_on=date(2026, 6, 1),
        )
        executor, factory = build_executor(tmp_path)
        seed(factory, wallet, plan)

        run = executor.execute(plan.plan_id, ANCHOR)

        assert run.status is RunStatus.SUCCEEDED
        after = wallet_after(factory, wallet.wallet_id)
        assert after.locked_balance == Money(Decimal("3500"), NGN)
        assert after.available_balance == Money(Decimal("2500"), NGN)

    def test_a_frozen_wallet_blocks_a_plan_mixing_release_and_payout(
        self, build_wallet, build_plan, tmp_path
    ):
        """The half-execution pre-flight prevents: release succeeds, payout fails."""
        wallet = build_wallet(status=WalletStatus.FROZEN, available="1000", locked="5000")
        plan = build_plan(
            wallet_id=wallet.wallet_id,
            instructions=(release("1500"), payout("2000")),
            ends_on=date(2026, 6, 1),
        )
        executor, factory = build_executor(tmp_path)
        seed(factory, wallet, plan)

        run = executor.execute(plan.plan_id, ANCHOR)

        assert run.reason is RunBlockReason.WALLET_FROZEN
        # The release must NOT have happened - that is the whole point.
        after = wallet_after(factory, wallet.wallet_id)
        assert after.locked_balance == Money(Decimal("5000"), NGN)
        assert after.available_balance == Money(Decimal("1000"), NGN)


class TestAtomicity:
    """A run is all or nothing, and the pre-flight is what makes that true."""

    def test_a_run_costing_more_than_the_balance_pays_nobody(
        self, build_wallet, build_plan, tmp_path
    ):
        """The half-paid payroll: five payroll lines, funds for three.

        Without a pre-flight check on the *total*, the first three would pay and
        the fourth would raise - three people paid, two not, and a plan whose
        record claims the run happened. Checking total_to_move up front makes
        that state unreachable.
        """
        wallet = build_wallet(locked="60000")
        plan = build_plan(
            wallet_id=wallet.wallet_id,
            instructions=tuple(payout("20000", f"salary {n}") for n in range(5)),
        )
        executor, factory = build_executor(tmp_path)
        seed(factory, wallet, plan)

        run = executor.execute(plan.plan_id, ANCHOR)

        assert run.status is RunStatus.BLOCKED
        assert run.reason is RunBlockReason.INSUFFICIENT_BALANCE
        assert ledger_of(factory, wallet.wallet_id) == []
        assert wallet_after(factory, wallet.wallet_id).locked_balance == Money(
            Decimal("60000"), NGN
        )

    def test_a_run_affordable_to_the_penny_goes_through(
        self, build_wallet, build_plan, tmp_path
    ):
        wallet = build_wallet(locked="100000")
        plan = build_plan(
            wallet_id=wallet.wallet_id,
            instructions=tuple(payout("20000", f"salary {n}") for n in range(5)),
        )
        executor, factory = build_executor(tmp_path)
        seed(factory, wallet, plan)

        run = executor.execute(plan.plan_id, ANCHOR)

        assert run.status is RunStatus.SUCCEEDED
        assert wallet_after(factory, wallet.wallet_id).locked_balance == Money(
            Decimal("0"), NGN
        )


class TestMoneyThatIsLockedButNotYetSpendable:
    """A pot's maturity date, seen from a run - the pre-flight's half of the rule.

    The pre-flight and the execution must answer the same question about the same
    money, and the failure mode when they do not is the half-executed run the
    pre-flight exists to prevent: funded on paper by a pot that cannot be spent
    yet, blocked only when the operation finally tries it. These tests are that
    agreement, stated from the outside.
    """

    def test_a_run_funded_only_by_an_immature_pot_is_blocked(
        self, build_wallet, build_plan, tmp_path
    ):
        """The wallet holds the money. The plan still cannot pay.

        Note the balance: 5,000 is locked and the plan needs 2,000, so every
        "is there enough money" question answered against ``locked_balance``
        says yes. The run must consult the *matured* total instead.
        """
        wallet = build_wallet(available="0")
        pot = wallet.open_fund(
            "Vacation", FundKind.PERSONAL, maturity_date=date(2027, 1, 1)
        )
        wallet.deposit_into_fund(pot.fund_id, Money(Decimal("5000"), NGN))
        plan = build_plan(wallet_id=wallet.wallet_id, instructions=(payout("2000"),))
        executor, factory = build_executor(tmp_path)
        seed(factory, wallet, plan)

        run = executor.execute(plan.plan_id, ANCHOR)

        assert run.status is RunStatus.BLOCKED
        assert run.reason is RunBlockReason.INSUFFICIENT_BALANCE
        # And nothing was half-done on the way to finding out.
        assert ledger_of(factory, wallet.wallet_id) == []
        assert wallet_after(factory, wallet.wallet_id).locked_balance == Money(
            Decimal("5000"), NGN
        )

    def test_the_same_plan_pays_once_the_pot_has_come_due(
        self, build_wallet, build_plan, tmp_path
    ):
        """The other side of the same boundary, and the point of the phase.

        The identical wallet and plan are run again with a later ``as_of`` and
        succeed - so the block above was about the date, not about the amount or
        about the wallet being unusable.
        """
        wallet = build_wallet(available="0")
        pot = wallet.open_fund(
            "Vacation", FundKind.PERSONAL, maturity_date=date(2026, 6, 1)
        )
        wallet.deposit_into_fund(pot.fund_id, Money(Decimal("5000"), NGN))
        plan = build_plan(wallet_id=wallet.wallet_id, instructions=(payout("2000"),))
        executor, factory = build_executor(tmp_path)
        seed(factory, wallet, plan)

        run = executor.execute(plan.plan_id, datetime(2026, 6, 1, 9, 0))

        assert run.status is RunStatus.SUCCEEDED
        assert wallet_after(factory, wallet.wallet_id).locked_balance == Money(
            Decimal("3000"), NGN
        )

    def test_the_funding_balance_counts_only_the_matured_pots(
        self, build_wallet, build_plan, tmp_path
    ):
        """One matured pot and one sealed one, and the run is judged on the first.

        A plan needing 2,000 against 1,000 matured and 9,000 sealed is blocked:
        being unable to spend money you can see is the correct answer, not a bug
        in the arithmetic, and it is reported as an ordinary insufficient balance
        because from the run's point of view that is what it is.
        """
        wallet = build_wallet(available="0")
        open_pot = wallet.open_fund("Salary", FundKind.PERSONAL)
        sealed = wallet.open_fund(
            "Vacation", FundKind.PERSONAL, maturity_date=date(2027, 1, 1)
        )
        wallet.deposit_into_fund(open_pot.fund_id, Money(Decimal("1000"), NGN))
        wallet.deposit_into_fund(sealed.fund_id, Money(Decimal("9000"), NGN))
        plan = build_plan(wallet_id=wallet.wallet_id, instructions=(payout("2000"),))
        executor, factory = build_executor(tmp_path)
        seed(factory, wallet, plan)

        run = executor.execute(plan.plan_id, ANCHOR)

        assert run.status is RunStatus.BLOCKED
        assert run.reason is RunBlockReason.INSUFFICIENT_BALANCE

    def test_a_release_plan_is_judged_on_matured_money_too(
        self, build_wallet, build_plan, tmp_path
    ):
        """The rule is not specific to payouts.

        A RELEASE instruction moves money back to the available balance, and it
        is a *release* that a maturity date most obviously governs - so the same
        maturing-only arithmetic has to apply to the pre-flight for it.
        """
        wallet = build_wallet(available="0")
        pot = wallet.open_fund(
            "Vacation", FundKind.PERSONAL, maturity_date=date(2027, 1, 1)
        )
        wallet.deposit_into_fund(pot.fund_id, Money(Decimal("5000"), NGN))
        plan = build_plan(
            wallet_id=wallet.wallet_id,
            instructions=(release("2000"),),
            ends_on=date(2026, 12, 1),
        )
        executor, factory = build_executor(tmp_path)
        seed(factory, wallet, plan)

        run = executor.execute(plan.plan_id, ANCHOR)

        assert run.status is RunStatus.BLOCKED
        assert wallet_after(factory, wallet.wallet_id).available_balance == Money(
            Decimal("0"), NGN
        )

    def test_an_open_pot_funds_a_run_at_any_moment(
        self, build_wallet, build_plan, tmp_path
    ):
        """``maturity_date = None`` means no maturity, so it is never the reason.

        The state every pre-existing locked balance is migrated into, and the one
        that makes the migration invisible: money that was spendable by a plan
        yesterday is spendable by that plan today.
        """
        wallet = build_wallet(available="0")
        pot = wallet.open_fund("Locked", FundKind.PERSONAL, maturity_date=None)
        wallet.deposit_into_fund(pot.fund_id, Money(Decimal("5000"), NGN))
        plan = build_plan(wallet_id=wallet.wallet_id, instructions=(payout("2000"),))
        executor, factory = build_executor(tmp_path)
        seed(factory, wallet, plan)

        run = executor.execute(plan.plan_id, ANCHOR)

        assert run.status is RunStatus.SUCCEEDED


# --- catching up -----------------------------------------------------------


class TestCatchingUp:
    def test_an_overdue_plan_runs_its_oldest_occurrence(
        self, build_wallet, build_plan, tmp_path
    ):
        """Checked in April, the plan still owes January first."""
        wallet = build_wallet(locked="100000")
        plan = build_plan(wallet_id=wallet.wallet_id, instructions=(payout("2000"),))
        executor, factory = build_executor(tmp_path)
        seed(factory, wallet, plan)

        run = executor.execute(plan.plan_id, datetime(2026, 4, 15))

        assert run.due_at == datetime(2026, 1, 1)

    def test_each_call_advances_exactly_one_occurrence(
        self, build_wallet, build_plan, tmp_path
    ):
        """One run per call is what bounds the burst after an outage."""
        wallet = build_wallet(locked="100000")
        plan = build_plan(wallet_id=wallet.wallet_id, instructions=(payout("2000"),))
        executor, factory = build_executor(tmp_path)
        seed(factory, wallet, plan)

        seen = [executor.execute(plan.plan_id, datetime(2026, 4, 15)).due_at for _ in range(4)]

        assert seen == [
            datetime(2026, 1, 1),
            datetime(2026, 2, 1),
            datetime(2026, 3, 1),
            datetime(2026, 4, 1),
        ]
        assert len(runs_of(factory, plan.plan_id)) == 4

    def test_the_backlog_stops_when_the_plan_is_current(
        self, build_wallet, build_plan, tmp_path
    ):
        wallet = build_wallet(locked="100000")
        plan = build_plan(wallet_id=wallet.wallet_id, instructions=(payout("2000"),))
        executor, factory = build_executor(tmp_path)
        seed(factory, wallet, plan)
        for _ in range(4):
            executor.execute(plan.plan_id, datetime(2026, 4, 15))

        assert executor.execute(plan.plan_id, datetime(2026, 4, 15)) is None


# --- finishing -------------------------------------------------------------


class TestReachingTheEnd:
    def test_a_run_past_the_end_date_completes_the_plan(
        self, build_wallet, build_plan, tmp_path
    ):
        wallet = build_wallet(locked="100000")
        plan = build_plan(
            wallet_id=wallet.wallet_id,
            instructions=(payout("2000"),),
            ends_on=date(2026, 2, 1),
        )
        executor, factory = build_executor(tmp_path)
        seed(factory, wallet, plan)

        executor.execute(plan.plan_id, datetime(2026, 1, 1))
        assert plan_after(factory, plan.plan_id).status is PlanStatus.ACTIVE

        executor.execute(plan.plan_id, datetime(2026, 2, 1))
        assert plan_after(factory, plan.plan_id).status is PlanStatus.COMPLETED

    def test_a_completed_plan_stops_running(self, build_wallet, build_plan, tmp_path):
        wallet = build_wallet(locked="100000")
        plan = build_plan(
            wallet_id=wallet.wallet_id,
            instructions=(payout("2000"),),
            ends_on=date(2026, 1, 1),
        )
        executor, factory = build_executor(tmp_path)
        seed(factory, wallet, plan)

        executor.execute(plan.plan_id, datetime(2026, 1, 1))

        assert executor.execute(plan.plan_id, datetime(2026, 6, 1)) is None


# --- retrying a blocked run ------------------------------------------------


class TestRetryingAfterABlock:
    def test_a_topped_up_and_resumed_plan_rewrites_its_blocked_row(
        self, build_wallet, build_plan, tmp_path
    ):
        """The natural key earning its keep: one occurrence, one row, one truth."""
        wallet = build_wallet(locked="500")
        plan = build_plan(wallet_id=wallet.wallet_id, instructions=(payout("2000"),))
        executor, factory = build_executor(tmp_path)
        seed(factory, wallet, plan)

        blocked = executor.execute(plan.plan_id, ANCHOR)
        assert blocked.status is RunStatus.BLOCKED

        # A human tops the wallet up and resumes the plan.
        top_up_locked(factory, wallet.wallet_id, "5000")
        resume(factory, plan.plan_id)

        paid = executor.execute(plan.plan_id, ANCHOR)

        assert paid.status is RunStatus.SUCCEEDED
        stored = runs_of(factory, plan.plan_id)
        assert len(stored) == 1
        assert stored[0].status is RunStatus.SUCCEEDED
        assert stored[0].reason is None

    def test_a_retried_run_pays_the_same_occurrence_not_the_next_one(
        self, build_wallet, build_plan, tmp_path
    ):
        wallet = build_wallet(locked="500")
        plan = build_plan(wallet_id=wallet.wallet_id, instructions=(payout("2000"),))
        executor, factory = build_executor(tmp_path)
        seed(factory, wallet, plan)
        executor.execute(plan.plan_id, ANCHOR)

        top_up_locked(factory, wallet.wallet_id, "5000")
        resume(factory, plan.plan_id)

        paid = executor.execute(plan.plan_id, datetime(2026, 4, 15))

        assert paid.due_at == datetime(2026, 1, 1)


class TestInstructionIdempotencyKeys:
    def test_each_instruction_gets_a_distinct_deterministic_reference(
        self, build_wallet, build_plan, tmp_path
    ):
        wallet = build_wallet(locked="100000")
        plan = build_plan(
            wallet_id=wallet.wallet_id,
            instructions=(payout("2000", "a"), payout("3000", "b")),
        )
        executor, factory = build_executor(tmp_path)
        seed(factory, wallet, plan)

        executor.execute(plan.plan_id, ANCHOR)

        references = sorted(
            entry.internal_reference for entry in ledger_of(factory, wallet.wallet_id)
        )
        assert references == [
            f"plan:{plan.plan_id}:2026-01-01T00:00:00:0",
            f"plan:{plan.plan_id}:2026-01-01T00:00:00:1",
        ]

    def test_next_months_run_uses_different_references(
        self, build_wallet, build_plan, tmp_path
    ):
        """The occurrence is part of the key, so February is a payment not a duplicate."""
        wallet = build_wallet(locked="100000")
        plan = build_plan(wallet_id=wallet.wallet_id, instructions=(payout("2000"),))
        executor, factory = build_executor(tmp_path)
        seed(factory, wallet, plan)

        executor.execute(plan.plan_id, ANCHOR)
        executor.execute(plan.plan_id, datetime(2026, 2, 1))

        entries = ledger_of(factory, wallet.wallet_id)
        assert len(entries) == 2
        assert {entry.internal_reference for entry in entries} == {
            f"plan:{plan.plan_id}:2026-01-01T00:00:00:0",
            f"plan:{plan.plan_id}:2026-02-01T00:00:00:0",
        }


# --- the receipt -----------------------------------------------------------


class TestTheRunQueuesItsReceipt:
    """The run's fourth write: the message saying what happened.

    A run moves money, records the run, advances the plan, and queues the receipt
    - all inside one transaction, before a single ``commit()``. This class is
    about that fourth write: that it happens on both outcomes, that it is silent
    when there is nowhere to send, and that a run cannot land without it.
    """

    def test_a_successful_run_queues_a_receipt(
        self, build_wallet, build_plan, tmp_path
    ):
        wallet = build_wallet(locked="10000")
        plan = build_plan(wallet_id=wallet.wallet_id, instructions=(payout("2000"),))
        executor, factory = build_executor(tmp_path, recipient=RECIPIENT)
        seed(factory, wallet, plan)

        executor.execute(plan.plan_id, ANCHOR)

        queued = notifications_of(factory)
        assert len(queued) == 1
        assert queued[0].kind is NotificationKind.PAYOUT_SUCCEEDED
        assert queued[0].subject_id == plan.plan_id
        assert queued[0].recipient == RECIPIENT

    def test_the_receipt_says_what_moved_and_where(
        self, build_wallet, build_plan, tmp_path
    ):
        wallet = build_wallet(locked="10000")
        plan = build_plan(wallet_id=wallet.wallet_id, instructions=(payout("2000"),))
        executor, factory = build_executor(tmp_path, recipient=RECIPIENT)
        seed(factory, wallet, plan)

        executor.execute(plan.plan_id, ANCHOR)

        body = notifications_of(factory)[0].body
        assert "2000.00 NGN" in body
        assert "Chinedu Okafor" in body

    def test_the_receipt_names_the_occurrence_it_paid(
        self, build_wallet, build_plan, tmp_path
    ):
        """The body says *which* occurrence, which is not the same as *when* it was paid.

        A backlog run in April pays January, and both facts are true and both
        matter: the message is stamped with the moment the run was recorded, and
        the body names the occurrence it settled. A receipt that only carried the
        clock would let a user read a two-month-old payment as today's, with
        nothing to tell it apart from February's.
        """
        wallet = build_wallet(locked="100000")
        plan = build_plan(wallet_id=wallet.wallet_id, instructions=(payout("2000"),))
        executor, factory = build_executor(tmp_path, recipient=RECIPIENT)
        seed(factory, wallet, plan)

        executor.execute(plan.plan_id, datetime(2026, 4, 15))

        receipt = notifications_of(factory)[0]
        assert "2026-01-01T00:00" in receipt.body
        # Stamped by the run, so it is emphatically *not* the occurrence.
        assert receipt.created_at != datetime(2026, 1, 1)

    def test_its_key_names_the_plan_and_the_occurrence(
        self, build_wallet, build_plan, tmp_path
    ):
        """The primary key, checked as text on the row that was actually written.

        The composer derives this key and ``event_key`` owns the format; what is
        worth pinning *here* is that the key reaching the database is the one
        derived from this run's plan and moment - a use case threading the wrong
        id through would otherwise only show up as a duplicate suppressed
        somewhere far away.
        """
        wallet = build_wallet(locked="10000")
        plan = build_plan(wallet_id=wallet.wallet_id, instructions=(payout("2000"),))
        executor, factory = build_executor(tmp_path, recipient=RECIPIENT)
        seed(factory, wallet, plan)

        executor.execute(plan.plan_id, ANCHOR)

        assert notifications_of(factory)[0].event_key == (
            f"payout_succeeded:plan:{plan.plan_id}:2026-01-01T00:00:00"
        )

    def test_a_blocked_run_queues_a_receipt_too(
        self, build_wallet, build_plan, tmp_path
    ):
        """The message a user most needs and least expects.

        A blocked run moves nothing, so the wallet is unchanged and no ledger row
        exists. Without this the only trace is a ``plan_runs`` row nobody reads
        unless the plan is already known to be stuck - and it is not, because
        this is how they would find out.
        """
        wallet = build_wallet(locked="500")
        plan = build_plan(wallet_id=wallet.wallet_id, instructions=(payout("2000"),))
        executor, factory = build_executor(tmp_path, recipient=RECIPIENT)
        seed(factory, wallet, plan)

        executor.execute(plan.plan_id, ANCHOR)

        queued = notifications_of(factory)
        assert len(queued) == 1
        assert queued[0].kind is NotificationKind.PAYOUT_BLOCKED
        assert "insufficient balance" in queued[0].body

    def test_with_no_recipient_the_run_happens_and_says_nothing(
        self, build_wallet, build_plan, tmp_path
    ):
        """The ordinary state of a fresh install, and not an error.

        Nothing is composed, so there is nothing to defer and no line to print -
        and the money still moves. A missing mail address is a reason to be
        silent, never a reason to refuse a payment.
        """
        wallet = build_wallet(locked="10000")
        plan = build_plan(wallet_id=wallet.wallet_id, instructions=(payout("2000"),))
        executor, factory = build_executor(tmp_path, recipient=None)
        seed(factory, wallet, plan)

        run = executor.execute(plan.plan_id, ANCHOR)

        assert run.status is RunStatus.SUCCEEDED
        assert notifications_of(factory) == []
        assert wallet_after(factory, wallet.wallet_id).locked_balance == Money(
            Decimal("8000"), NGN
        )

    def test_a_blocked_run_with_no_recipient_is_also_silent(
        self, build_wallet, build_plan, tmp_path
    ):
        wallet = build_wallet(locked="500")
        plan = build_plan(wallet_id=wallet.wallet_id, instructions=(payout("2000"),))
        executor, factory = build_executor(tmp_path, recipient=None)
        seed(factory, wallet, plan)

        executor.execute(plan.plan_id, ANCHOR)

        assert notifications_of(factory) == []
        assert runs_of(factory, plan.plan_id)[0].status is RunStatus.BLOCKED

    def test_one_run_is_one_receipt_however_many_instructions(
        self, build_wallet, build_plan, tmp_path
    ):
        """Five payroll lines, one message - see decision 29.

        A run is one thing that happened to the person reading the mail, and five
        emails arriving together would have to be reassembled by hand to answer
        the question they are actually asking.
        """
        wallet = build_wallet(locked="100000")
        plan = build_plan(
            wallet_id=wallet.wallet_id,
            instructions=tuple(payout("20000", f"salary {n}") for n in range(5)),
        )
        executor, factory = build_executor(tmp_path, recipient=RECIPIENT)
        seed(factory, wallet, plan)

        executor.execute(plan.plan_id, ANCHOR)

        queued = notifications_of(factory)
        assert len(queued) == 1
        # Five lines, each naming the account it pays - one for every instruction.
        assert queued[0].body.count("  salary") == 5
        assert "Total moved: 100000.00 NGN." in queued[0].body


class TestOneOccurrenceCanBeTwoEvents:
    """Blocked at noon, paid in April: two outcomes, one ``plan_runs`` row, two receipts.

    The counterpart of ``test_a_topped_up_and_resumed_plan_rewrites_its_blocked_row``
    above, which says the *row* is rewritten rather than appended. The receipt
    table makes the opposite demand, and both are right: the run is one fact, and
    the two things that happened to the user are two. The kind in the derived key
    is what lets one table hold both.
    """

    def test_a_blocked_run_that_is_later_paid_queues_both_receipts(
        self, build_wallet, build_plan, tmp_path
    ):
        wallet = build_wallet(locked="500")
        plan = build_plan(wallet_id=wallet.wallet_id, instructions=(payout("2000"),))
        executor, factory = build_executor(tmp_path, recipient=RECIPIENT)
        seed(factory, wallet, plan)

        executor.execute(plan.plan_id, ANCHOR)
        top_up_locked(factory, wallet.wallet_id, "5000")
        resume(factory, plan.plan_id)
        executor.execute(plan.plan_id, ANCHOR)

        queued = notifications_of(factory)
        assert len(queued) == 2
        assert {one.kind for one in queued} == {
            NotificationKind.PAYOUT_BLOCKED,
            NotificationKind.PAYOUT_SUCCEEDED,
        }
        assert len(runs_of(factory, plan.plan_id)) == 1  # but one run row

    def test_the_same_block_twice_is_one_receipt(
        self, build_wallet, build_plan, tmp_path
    ):
        """Resumed unfunded and blocked again: the user has already been told.

        Announcing it on every resume would teach them to ignore the one message
        that means their money did not arrive. Nothing here reads before it
        writes - the second enqueue derives the key already in the table and
        inserts nothing.
        """
        wallet = build_wallet(locked="500")
        plan = build_plan(wallet_id=wallet.wallet_id, instructions=(payout("2000"),))
        executor, factory = build_executor(tmp_path, recipient=RECIPIENT)
        seed(factory, wallet, plan)

        executor.execute(plan.plan_id, ANCHOR)
        resume(factory, plan.plan_id)
        executor.execute(plan.plan_id, ANCHOR)

        assert len(notifications_of(factory)) == 1

    def test_a_second_month_is_a_second_receipt(
        self, build_wallet, build_plan, tmp_path
    ):
        """The occurrence is in the key, so January does not suppress February."""
        wallet = build_wallet(locked="100000")
        plan = build_plan(wallet_id=wallet.wallet_id, instructions=(payout("2000"),))
        executor, factory = build_executor(tmp_path, recipient=RECIPIENT)
        seed(factory, wallet, plan)

        executor.execute(plan.plan_id, ANCHOR)
        executor.execute(plan.plan_id, datetime(2026, 2, 1))

        keys = {one.event_key for one in notifications_of(factory)}
        assert keys == {
            f"payout_succeeded:plan:{plan.plan_id}:2026-01-01T00:00:00",
            f"payout_succeeded:plan:{plan.plan_id}:2026-02-01T00:00:00",
        }


class TestNoPayoutWithoutAReceipt:
    """The atomicity claim, checked by breaking the commit and looking at what is left.

    Decision 28 in one test: if the receipt cannot be written, the *payout* does
    not happen. That is deliberate rather than alarming - the plan's counter
    rolls back with it, so the next tick derives the same occurrence and pays
    then. Fail-loud costs one tick and buys a run that can never commit without
    the message that says so.
    """

    def test_a_failed_commit_leaves_neither_the_run_nor_its_receipt(
        self, build_wallet, build_plan, tmp_path
    ):
        """Four writes, one transaction - so all four are missing, not three."""
        wallet = build_wallet(locked="10000")
        plan = build_plan(wallet_id=wallet.wallet_id, instructions=(payout("2000"),))
        factory = SqliteUnitOfWorkFactory(str(tmp_path / "exploding.db"))
        seed(factory, wallet, plan)
        executor = ExecutePlanRun(ExplodingCommitFactory(factory), recipient=RECIPIENT)

        with pytest.raises(RuntimeError, match="commit failed"):
            executor.execute(plan.plan_id, ANCHOR)

        after = read(factory, lambda uow: uow.wallets.get_by_id(wallet.wallet_id))
        assert notifications_of(factory) == []
        assert runs_of(factory, plan.plan_id) == []
        assert ledger_of(factory, wallet.wallet_id) == []
        assert plan_after(factory, plan.plan_id).completed_runs == 0
        assert after.locked_balance == Money(Decimal("10000"), NGN)

    def test_a_blocked_run_rolls_back_its_pause_and_its_receipt_together(
        self, build_wallet, build_plan, tmp_path
    ):
        """The blocked path commits in its own method, so it needs its own check.

        The plan is *paused* by a block, which is a write like any other - and a
        plan that came back paused from a rolled-back run would be a plan holding
        a block that no longer exists in the record.
        """
        wallet = build_wallet(locked="500")
        plan = build_plan(wallet_id=wallet.wallet_id, instructions=(payout("2000"),))
        factory = SqliteUnitOfWorkFactory(str(tmp_path / "exploding.db"))
        seed(factory, wallet, plan)
        executor = ExecutePlanRun(ExplodingCommitFactory(factory), recipient=RECIPIENT)

        with pytest.raises(RuntimeError, match="commit failed"):
            executor.execute(plan.plan_id, ANCHOR)

        assert notifications_of(factory) == []
        assert runs_of(factory, plan.plan_id) == []
        assert plan_after(factory, plan.plan_id).status is PlanStatus.ACTIVE

    def test_the_tick_after_a_failed_attempt_pays_the_same_occurrence(
        self, build_wallet, build_plan, tmp_path
    ):
        """Which is what makes the rollback survivable rather than merely safe.

        The failed commit above cost one tick, not one payment: the counter never
        advanced, so the next attempt derives the same occurrence and pays it.
        """
        wallet = build_wallet(locked="10000")
        plan = build_plan(wallet_id=wallet.wallet_id, instructions=(payout("2000"),))
        factory = SqliteUnitOfWorkFactory(str(tmp_path / "recovered.db"))
        seed(factory, wallet, plan)
        broken = ExecutePlanRun(ExplodingCommitFactory(factory), recipient=RECIPIENT)

        with pytest.raises(RuntimeError):
            broken.execute(plan.plan_id, ANCHOR)

        working = ExecutePlanRun(factory, recipient=RECIPIENT)
        paid = working.execute(plan.plan_id, ANCHOR)

        assert paid.status is RunStatus.SUCCEEDED
        assert paid.due_at == ANCHOR
        assert len(notifications_of(factory)) == 1

