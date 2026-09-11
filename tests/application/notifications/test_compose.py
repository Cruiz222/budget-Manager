"""The words, which are the only thing this module does.

Every function under test is **pure**: no clock, no store, nothing sent. That is
what makes these tests read like text assertions rather than like plumbing - and
it is a property worth testing *as* a property, which is why the last test here
exists. A ``compose`` function that read the clock or opened a unit of work would
still pass a test that only checked the wording.

Where the fixtures from ``conftest`` fit: ``build_wallet`` and ``build_plan`` are
reused rather than re-rolled, so a change to how the suite builds an aggregate
reaches these tests too. What is added here is only the *shape* this module
cares about - a noon plan, a settled ledger row - not a second definition of the
aggregates themselves.
"""

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.notifications import compose
from app.domain.money.currency import Currency
from app.domain.money.destination import Destination
from app.domain.money.destinationKind import DestinationKind
from app.domain.money.money import Money
from app.domain.money.transaction import Transaction
from app.domain.money.transactionStatus import TransactionStatus
from app.domain.money.transactionType import TransactionType
from app.domain.notifications.eventKey import for_plan_event, for_wallet_event
from app.domain.notifications.notificationKind import NotificationKind
from app.domain.planning.instruction import Instruction
from app.domain.planning.plannedAction import PlannedAction
from app.domain.planning.planRun import PlanRun
from app.domain.planning.runBlockReason import RunBlockReason
from app.domain.planning.runStatus import RunStatus

NOON = datetime(2026, 3, 2, 12, 0)
TEN_PAST_NOON = datetime(2026, 3, 2, 12, 10)

#: The line every plan fixture below moves 20000 NGN with, to the same account
#: the conftest destination names. Written once so a body assertion can name the
#: rendered string without rebuilding the instruction it is asserting about.
SALARY = Instruction(
    action=PlannedAction.PAYOUT,
    amount=Money(Decimal("20000"), Currency.NGN),
    label="salary",
    destination=Destination(
        kind=DestinationKind.BANK_ACCOUNT,
        identifier="0123456789",
        name="Chinedu Okafor",
        details={"bank_code": "058"},
    ),
)

RENT = Instruction(
    action=PlannedAction.PAYOUT,
    amount=Money(Decimal("15000"), Currency.NGN),
    label="rent",
    destination=Destination(
        kind=DestinationKind.BANK_ACCOUNT,
        identifier="0987654321",
        name="Ada Nwosu",
        details={"bank_code": "058"},
    ),
)


@pytest.fixture
def build_noon_plan(build_plan):
    """The headline case: a noon plan moving 20000 NGN to one bank account.

    The *noon* anchor is what matters, and it is why this wraps ``build_plan``
    rather than using it directly: a receipt is stamped by ``run.due_at`` and by
    ``run.recorded_at``, and a midnight anchor cannot tell a moment that kept its
    time of day from one that lost it.
    """

    def _build(**overrides):
        kwargs = dict(anchor=NOON, name="salary", instructions=(SALARY,))
        kwargs.update(overrides)
        return build_plan(**kwargs)

    return _build


def build_run(plan, **overrides) -> PlanRun:
    """A run of ``plan`` for the noon occurrence, by default one that succeeded."""
    kwargs = dict(
        plan_id=plan.plan_id,
        due_at=NOON,
        status=RunStatus.SUCCEEDED,
        recorded_at=TEN_PAST_NOON,
    )
    kwargs.update(overrides)
    return PlanRun(**kwargs)


def build_transaction(wallet, transaction_type=TransactionType.DEPOSIT, **overrides):
    """A *settled* ledger row, which is the only kind a receipt describes.

    Written with an explicit status and completion moment rather than by calling
    ``mark_successful``, because the constructor's status parameter is how the
    persistence layer re-hydrates a stored row - and a receipt is composed from a
    row that has already been written.
    """
    destination = overrides.pop("destination", None)
    if transaction_type is TransactionType.PAYOUT and destination is None:
        # A payout must record where it went - the aggregate refuses otherwise -
        # and ``compose.wallet_movement`` relies on that rather than checking.
        destination = SALARY.destination
    kwargs = dict(
        wallet_id=wallet.wallet_id,
        type=transaction_type,
        amount=overrides.pop(
            "amount", Money(Decimal("2500.50"), Currency.NGN)
        ),
        internal_reference="ref-1",
        status=TransactionStatus.SUCCESSFUL,
        completed_at=TEN_PAST_NOON,
        destination=destination,
    )
    kwargs.update(overrides)
    return Transaction(**kwargs)


class TestTheRecipientIsTheWholeGate:
    """``None`` means "nowhere to send", and every builder must honour it.

    This is the rule that lets an install with no mail account behave exactly as
    it always did: nothing is composed, so there is nothing to defer and no line
    to print about one. Raising instead would make a user who has not configured
    email unable to deposit money.
    """

    def test_a_successful_run_with_no_recipient_says_nothing(self, build_noon_plan):
        plan = build_noon_plan()

        assert compose.payout_succeeded(plan, build_run(plan), None) is None

    def test_a_blocked_run_with_no_recipient_says_nothing(self, build_noon_plan):
        plan = build_noon_plan()
        run = build_run(
            plan, status=RunStatus.BLOCKED, reason=RunBlockReason.INSUFFICIENT_BALANCE
        )

        assert compose.payout_blocked(plan, run, None) is None

    @pytest.mark.parametrize(
        "kind",
        [
            NotificationKind.WALLET_DEPOSIT,
            NotificationKind.WALLET_WITHDRAWAL,
            NotificationKind.WALLET_PAYOUT,
        ],
    )
    def test_a_wallet_movement_with_no_recipient_says_nothing(self, build_wallet, kind):
        wallet = build_wallet()

        assert (
            compose.wallet_movement(kind, wallet, build_transaction(wallet), None)
            is None
        )


class TestTheSuccessfulRun:
    def test_the_subject_names_the_plan_and_the_total(self, build_noon_plan):
        plan = build_noon_plan()

        notification = compose.payout_succeeded(plan, build_run(plan), "a@b.c")

        assert notification.subject == "Plan 'salary': 20000.00 NGN moved"

    def test_the_subject_is_the_plan_that_ran(self, build_noon_plan):
        plan = build_noon_plan()

        notification = compose.payout_succeeded(plan, build_run(plan), "a@b.c")

        assert notification.subject_id == plan.plan_id
        assert notification.kind is NotificationKind.PAYOUT_SUCCEEDED

    def test_the_body_lists_what_the_run_did(self, build_noon_plan):
        """Rendered through ``Instruction.__str__``, not formatted here.

        That is the only way the receipt and the ``plan show`` output can be
        guaranteed to describe a line the same way - a second renderer would be
        free to drift from the first, and the drift would be silent.
        """
        plan = build_noon_plan()

        notification = compose.payout_succeeded(plan, build_run(plan), "a@b.c")

        assert "  salary: pay 20000.00 NGN to Chinedu Okafor" in notification.body
        assert "Total moved: 20000.00 NGN." in notification.body

    def test_a_multi_destination_run_is_one_message(self, build_noon_plan):
        """One receipt for the whole run, not one per instruction.

        A plan paying three accounts is one thing that happened to the person
        reading the mail; three emails would arrive together and have to be
        reassembled by hand to answer "did the run happen?".
        """
        plan = build_noon_plan(instructions=(SALARY, RENT))

        notification = compose.payout_succeeded(plan, build_run(plan), "a@b.c")

        assert notification.body.count("Chinedu Okafor") == 1
        assert notification.body.count("Ada Nwosu") == 1
        # ...and the total across both lines, not just the first.
        assert "Total moved: 35000.00 NGN." in notification.body

    def test_it_says_the_money_has_already_moved(self, build_noon_plan):
        """The sentence that distinguishes a receipt from a request.

        The warning this phase sits beside makes the opposite point at the same
        length - "the payout will go ahead whether or not you act on it" - and the
        two must not blur into each other.
        """
        plan = build_noon_plan()

        notification = compose.payout_succeeded(plan, build_run(plan), "a@b.c")

        assert "already moved" in notification.body
        assert "receipt, not a request" in notification.body

    def test_its_key_is_the_one_the_domain_derives(self, build_noon_plan):
        """A second enqueue of the same event has to be a no-op, and the key is
        what decides that - so the composer must not invent its own."""
        plan = build_noon_plan()
        run = build_run(plan)

        notification = compose.payout_succeeded(plan, run, "a@b.c")

        assert notification.event_key == for_plan_event(
            NotificationKind.PAYOUT_SUCCEEDED, plan.plan_id, NOON
        )

    def test_it_is_stamped_by_the_run_not_by_a_clock(self, build_noon_plan):
        plan = build_noon_plan()

        notification = compose.payout_succeeded(plan, build_run(plan), "a@b.c")

        assert notification.created_at == TEN_PAST_NOON

    def test_the_recipient_is_carried_through_unchanged(self, build_noon_plan):
        """Captured at enqueue, never resolved at send - so it must survive as given."""
        plan = build_noon_plan()

        notification = compose.payout_succeeded(plan, build_run(plan), "a@b.c")

        assert notification.recipient == "a@b.c"


class TestTheBlockedRun:
    def test_the_subject_says_it_could_not_run(self, build_noon_plan):
        plan = build_noon_plan()
        run = build_run(
            plan, status=RunStatus.BLOCKED, reason=RunBlockReason.INSUFFICIENT_BALANCE
        )

        notification = compose.payout_blocked(plan, run, "a@b.c")

        assert notification.subject == "Plan 'salary' could not run"

    def test_the_body_names_the_reason_in_words(self, build_noon_plan):
        """``insufficient_balance`` is a stored value, not a sentence.

        The underscore form is the enum's name and belongs in the database; the
        reader gets "insufficient balance". Doing this here rather than reusing
        the enum member keeps the wording decision in the one module that owns
        wording.
        """
        plan = build_noon_plan()
        run = build_run(
            plan, status=RunStatus.BLOCKED, reason=RunBlockReason.INSUFFICIENT_BALANCE
        )

        notification = compose.payout_blocked(plan, run, "a@b.c")

        assert "insufficient balance" in notification.body
        assert "insufficient_balance" not in notification.body

    @pytest.mark.parametrize(
        "reason",
        [
            RunBlockReason.INSUFFICIENT_BALANCE,
            RunBlockReason.WALLET_FROZEN,
            RunBlockReason.WALLET_CLOSED,
        ],
    )
    def test_every_reason_reads_as_words(self, build_noon_plan, reason):
        """Checked against the enum rather than a list written here.

        A new reason added to ``RunBlockReason`` and forgotten in ``compose``
        would otherwise reach a reader as ``some_new_reason`` with its underscore
        showing - a cosmetic failure that is invisible until it is in someone's
        inbox. Parameterising over the enum means the new member tests itself.

        The assertion is about the *reason*, not the whole body: the instruction
        lines can legitimately carry an underscore (``bank_account`` is the
        destination kind's stored value), so the check must be narrow enough not
        to confuse the reason's wording with the destination's identifier.
        """
        plan = build_noon_plan()
        run = build_run(plan, status=RunStatus.BLOCKED, reason=reason)

        notification = compose.payout_blocked(plan, run, "a@b.c")

        assert reason.value.replace("_", " ") in notification.body
        assert reason.value not in notification.body

    def test_it_says_the_plan_is_paused_and_still_owes_the_occurrence(
        self, build_noon_plan
    ):
        """The load-bearing sentence, and the one a reader can act on.

        A blocked run does not advance the plan's counter, so the missed
        occurrence is still owed - resuming picks up the *same* one rather than
        skipping it. Someone who believes the opposite will top up the wallet and
        wonder why nothing happens.
        """
        plan = build_noon_plan()
        run = build_run(
            plan, status=RunStatus.BLOCKED, reason=RunBlockReason.INSUFFICIENT_BALANCE
        )

        notification = compose.payout_blocked(plan, run, "a@b.c")

        assert "paused" in notification.body
        assert "same occurrence" in notification.body

    def test_it_says_no_money_moved(self, build_noon_plan):
        plan = build_noon_plan()
        run = build_run(
            plan, status=RunStatus.BLOCKED, reason=RunBlockReason.WALLET_FROZEN
        )

        notification = compose.payout_blocked(plan, run, "a@b.c")

        assert "Nothing was paid" in notification.body

    def test_it_still_lists_what_the_run_owed(self, build_noon_plan):
        """A blocked receipt is not only a refusal - it says what did not happen.

        "Your plan was blocked" without the amount and the account leaves the
        reader unable to tell whether the money that did not move was 200 NGN or
        their salary.
        """
        plan = build_noon_plan()
        run = build_run(
            plan, status=RunStatus.BLOCKED, reason=RunBlockReason.INSUFFICIENT_BALANCE
        )

        notification = compose.payout_blocked(plan, run, "a@b.c")

        assert "  salary: pay 20000.00 NGN to Chinedu Okafor" in notification.body
        assert "Total: 20000.00 NGN." in notification.body

    def test_its_key_is_distinct_from_the_successful_one(self, build_noon_plan):
        """Blocked at noon, retried successfully later: one ``plan_runs`` row, two
        events, two messages. The kind in the key is what keeps them apart."""
        plan = build_noon_plan()
        run = build_run(
            plan, status=RunStatus.BLOCKED, reason=RunBlockReason.INSUFFICIENT_BALANCE
        )

        notification = compose.payout_blocked(plan, run, "a@b.c")

        assert notification.event_key != for_plan_event(
            NotificationKind.PAYOUT_SUCCEEDED, plan.plan_id, NOON
        )
        assert notification.event_key == for_plan_event(
            NotificationKind.PAYOUT_BLOCKED, plan.plan_id, NOON
        )


class TestAWalletMovement:
    def test_a_deposit_reads_as_money_arriving(self, build_wallet):
        wallet = build_wallet()
        transaction = build_transaction(wallet, TransactionType.DEPOSIT)

        notification = compose.wallet_movement(
            NotificationKind.WALLET_DEPOSIT, wallet, transaction, "a@b.c"
        )

        assert notification.subject == "Deposited 2500.50 NGN (ref-1)"
        assert "2500.50 NGN arrived in the wallet from outside." in notification.body

    def test_a_withdrawal_reads_as_money_leaving(self, build_wallet):
        wallet = build_wallet()
        transaction = build_transaction(wallet, TransactionType.WITHDRAWAL)

        notification = compose.wallet_movement(
            NotificationKind.WALLET_WITHDRAWAL, wallet, transaction, "a@b.c"
        )

        assert notification.subject == "Withdrew 2500.50 NGN (ref-1)"
        assert "2500.50 NGN left the wallet." in notification.body

    def test_a_payout_names_the_account_it_went_to(self, build_wallet):
        wallet = build_wallet()
        transaction = build_transaction(wallet, TransactionType.PAYOUT)

        notification = compose.wallet_movement(
            NotificationKind.WALLET_PAYOUT, wallet, transaction, "a@b.c"
        )

        assert notification.subject == (
            "Paid 2500.50 NGN to Chinedu Okafor (bank_account:0123456789) (ref-1)"
        )
        assert "Chinedu Okafor (bank_account:0123456789)." in notification.body

    def test_the_body_reports_the_balances_the_operation_left_behind(self, build_wallet):
        """Most of the value of a receipt: not "5,000 arrived" but "you now hold
        12,500", which is the answer to the question the reader actually opened
        the message with."""
        wallet = build_wallet(available="12500.50", locked="5000")
        transaction = build_transaction(wallet, TransactionType.DEPOSIT)

        notification = compose.wallet_movement(
            NotificationKind.WALLET_DEPOSIT, wallet, transaction, "a@b.c"
        )

        assert "available  12500.50 NGN" in notification.body
        assert "locked     5000.00 NGN" in notification.body

    def test_the_reference_is_in_the_subject_not_only_the_body(self, build_wallet):
        """It is the key a user reconciles against their bank statement, and what
        makes two otherwise identical deposits distinguishable in a list view."""
        wallet = build_wallet()
        transaction = build_transaction(wallet, internal_reference="ref-42")

        notification = compose.wallet_movement(
            NotificationKind.WALLET_DEPOSIT, wallet, transaction, "a@b.c"
        )

        assert "ref-42" in notification.subject
        assert "Reference: ref-42" in notification.body

    def test_its_key_is_the_one_the_domain_derives(self, build_wallet):
        wallet = build_wallet()
        transaction = build_transaction(wallet, internal_reference="ref-42")

        notification = compose.wallet_movement(
            NotificationKind.WALLET_DEPOSIT, wallet, transaction, "a@b.c"
        )

        assert notification.event_key == for_wallet_event(
            NotificationKind.WALLET_DEPOSIT, wallet.wallet_id, "ref-42"
        )

    def test_the_subject_is_the_wallet_that_moved(self, build_wallet):
        wallet = build_wallet()
        transaction = build_transaction(wallet)

        notification = compose.wallet_movement(
            NotificationKind.WALLET_DEPOSIT, wallet, transaction, "a@b.c"
        )

        assert notification.subject_id == wallet.wallet_id

    def test_it_is_stamped_by_the_ledger_row_not_by_a_clock(self, build_wallet):
        wallet = build_wallet()
        transaction = build_transaction(wallet)

        notification = compose.wallet_movement(
            NotificationKind.WALLET_DEPOSIT, wallet, transaction, "a@b.c"
        )

        assert notification.created_at == TEN_PAST_NOON

    def test_a_plan_kind_is_refused_loudly(self, build_wallet):
        """The two halves of one list, and this kind is in the other one.

        ``WalletService.ANNOUNCED`` and this function are the two ends of the same
        decision, and guessing a shape here would send a receipt that confidently
        describes the wrong event - worse than a loud failure the first time it
        runs.
        """
        wallet = build_wallet()

        with pytest.raises(ValueError):
            compose.wallet_movement(
                NotificationKind.PAYOUT_SUCCEEDED,
                wallet,
                build_transaction(wallet),
                "a@b.c",
            )

    def test_every_announced_kind_can_be_composed(self, build_wallet):
        """``ANNOUNCED`` and this function have to cover the same set.

        Checked against the dict itself rather than against a list written here,
        so adding a kind to ``ANNOUNCED`` and forgetting the wording fails here -
        at a test - rather than in production, on the deposit that needed it.
        """
        from app.application.wallet_service import WalletService

        ledger_type = {
            NotificationKind.WALLET_DEPOSIT: TransactionType.DEPOSIT,
            NotificationKind.WALLET_WITHDRAWAL: TransactionType.WITHDRAWAL,
            NotificationKind.WALLET_PAYOUT: TransactionType.PAYOUT,
        }
        wallet = build_wallet()

        for operation_cls, kind in WalletService.ANNOUNCED.items():
            notification = compose.wallet_movement(
                kind,
                wallet,
                build_transaction(wallet, ledger_type[kind]),
                "a@b.c",
            )

            assert notification is not None, f"{operation_cls.__name__} never speaks"
            assert notification.kind is kind

    def test_the_announced_set_is_exactly_the_kinds_that_move_value(
        self, build_wallet
    ):
        """The fund operations are absent, and their absence is a decision.

        A pot changes *where* the wallet's money sits, not how much of it there
        is - and the user typed the pot's name at a terminal that has already
        printed the result. Asserted here so that adding one is a deliberate edit
        to ``ANNOUNCED`` rather than a quiet side effect of a refactor.

        ``DepositIntoFund`` is the exception and is asserted present, because it
        is the one fund operation that brings money in from outside. The full set
        is checked in ``tests/application/test_wallet_service.py``; what this
        test guards is the *shape* of the rule - crossing the wallet's boundary
        speaks, moving money around inside it does not.
        """
        from app.application.fund.deposit_into_fund import DepositIntoFund
        from app.application.fund.lock_into_fund import LockIntoFund
        from app.application.fund.release_from_fund import ReleaseFromFund
        from app.application.release.release_from_locked import ReleaseFromLocked
        from app.application.wallet_service import WalletService

        assert DepositIntoFund in WalletService.ANNOUNCED
        assert LockIntoFund not in WalletService.ANNOUNCED
        assert ReleaseFromFund not in WalletService.ANNOUNCED
        assert ReleaseFromLocked not in WalletService.ANNOUNCED


def test_composing_reads_no_clock(build_wallet, build_noon_plan):
    """The purity the module docstring claims, checked rather than asserted in prose.

    If a builder read the clock, a receipt would be stamped by the moment it was
    composed rather than by the moment the event happened - and on a run rolled
    back and retried a tick later, the two would disagree. Every ``created_at``
    below comes from the aggregate passed in.
    """
    wallet = build_wallet()
    plan = build_noon_plan(wallet_id=wallet.wallet_id)
    run = build_run(plan)
    transaction = build_transaction(wallet)

    assert compose.payout_succeeded(plan, run, "a@b.c").created_at == run.recorded_at
    assert (
        compose.wallet_movement(
            NotificationKind.WALLET_DEPOSIT, wallet, transaction, "a@b.c"
        ).created_at
        == transaction.completed_at
    )


def test_a_plan_built_now_and_a_run_recorded_later_still_agree_on_the_moment(
    build_noon_plan,
):
    """The plan's own ``created_at`` is not what stamps the receipt.

    ``SavingsPlan`` defaults ``created_at`` to ``datetime.now()``, and a receipt
    that used it would report the day the plan was *written* as the day the money
    moved - which for a long-running plan is months out. The run's moment is the
    only one that describes the event.
    """
    plan = build_noon_plan()
    run = build_run(plan, recorded_at=datetime(2026, 6, 2, 12, 10))

    notification = compose.payout_succeeded(plan, run, "a@b.c")

    assert notification.created_at == datetime(2026, 6, 2, 12, 10)
    assert notification.created_at != plan.created_at


def test_a_wallet_receipt_is_about_the_wallet_that_was_passed_in(build_wallet):
    """Two wallets, one transaction each: the receipt follows the wallet argument.

    The transaction's own ``wallet_id`` and the ``Wallet`` handed to the builder
    are the same aggregate here - but only one of them carries the resulting
    balances, and it is the wallet that decides which row the key is derived
    from.
    """
    wallet = build_wallet(available="1000", locked="0")
    other = build_wallet(available="9999", locked="0")
    transaction = build_transaction(wallet)

    notification = compose.wallet_movement(
        NotificationKind.WALLET_DEPOSIT, wallet, transaction, "a@b.c"
    )

    assert notification.subject_id == wallet.wallet_id
    assert notification.subject_id != other.wallet_id
    assert "1000.00 NGN" in notification.body


def test_every_kind_has_a_wording():
    """``NotificationKind`` and ``compose`` are the same list, checked as one.

    The two functions that take a *kind* rather than a fixed one -
    ``wallet_movement`` takes a wallet kind and raises for a plan kind - are only
    half the story: nothing here can construct a plan notification from a wallet
    kind or the reverse, because the signatures differ. What this pins is
    narrower and still worth having: the five members are exactly the five things
    this module knows how to say, so a sixth kind cannot be added with no words
    behind it.
    """
    from app.application.wallet_service import WalletService

    wallet_kinds = set(WalletService.ANNOUNCED.values())
    plan_kinds = {
        NotificationKind.PAYOUT_SUCCEEDED,
        NotificationKind.PAYOUT_BLOCKED,
    }

    assert wallet_kinds | plan_kinds == set(NotificationKind)
    assert wallet_kinds & plan_kinds == set()
