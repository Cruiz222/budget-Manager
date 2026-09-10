from datetime import date, datetime
from decimal import Decimal

from app.application.planning.execute_plan_run import ExecutePlanRun
from app.domain.money.currency import Currency
from app.domain.money.destination import Destination
from app.domain.money.destinationKind import DestinationKind
from app.domain.money.money import Money
from app.domain.money.transactionType import TransactionType
from app.domain.money.walletStatus import WalletStatus
from app.domain.planning.instruction import Instruction
from app.domain.planning.plannedAction import PlannedAction
from app.domain.planning.planSource import PlanSource
from app.domain.planning.planStatus import PlanStatus
from app.domain.planning.runBlockReason import RunBlockReason
from app.domain.planning.runStatus import RunStatus
from app.infrastructure.persistence.sqlite_unit_of_work import SqliteUnitOfWorkFactory

NGN = Currency.NGN

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


def build_executor(tmp_path, name="plans.db"):
    factory = SqliteUnitOfWorkFactory(str(tmp_path / name))
    return ExecutePlanRun(factory), factory


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


def top_up_locked(factory, wallet_id, amount):
    """Deposit and then lock, so the money lands in the balance the plan spends.

    A plain deposit would not do: it credits the *available* balance, and a
    LOCKED-source plan is funded by locked money only. A test that deposited and
    expected the plan to run would be testing the fallback the design forbids.
    """
    uow = factory.start()
    try:
        wallet = uow.wallets.get_by_id(wallet_id)
        wallet.apply_deposit(Money(Decimal(amount), NGN))
        wallet.lock_funds(Money(Decimal(amount), NGN))
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

    A frozen wallet permits release_funds - money does not leave, it only moves
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
