"""``SettlePayment``: the one money use case that acts for nobody.

The file is arranged as the event table is, because the table *is* the feature.
Four events, and the two middle rows are the ones worth reading twice: a transfer
that succeeded credits nothing (the wallet was debited when it was requested) and
a transfer that failed gives that debit back. Everything about failure returns as
a value rather than as an exception - a webhook cannot answer with a 500 and
expect the provider to stop - and the refusals are asserted here the same way the
movements are.

**What this file deliberately does not prove** is anything about a signature or a
request body. It hands ``settle`` a ``ProviderOutcome`` and asks what it did,
which is the whole boundary: by the time this use case runs, the parsing and the
verification are finished, and a test that reached past that would be testing the
route through the service.
"""

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from app.application.payments.settle_payment import SettlePayment
from app.application.payments.settledPayment import SettlementOutcome
from app.domain.money.currency import Currency
from app.domain.money.destination import Destination
from app.domain.money.destinationKind import DestinationKind
from app.domain.money.money import Money
from app.domain.money.transaction import Transaction
from app.domain.money.transactionStatus import TransactionStatus
from app.domain.money.transactionType import TransactionType
from app.domain.money.walletStatus import WalletStatus
from app.domain.notifications.notificationKind import NotificationKind
from app.domain.payments.providerEvent import ProviderEvent
from app.domain.payments.providerOutcome import ProviderOutcome
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)

NGN = Currency.NGN

#: Where a receipt is addressed, for the tests that are about the receipt. The
#: same shape ``test_wallet_service.py`` uses, and passed rather than read from
#: the environment for the same reason: a use case takes its inputs.
RECIPIENT = "chinedu@example.com"

DESTINATION = Destination(
    kind=DestinationKind.BANK_ACCOUNT,
    identifier="0123456789",
    name="Chinedu Okafor",
    details={"bank_code": "058"},
)


def a_settler(tmp_path, recipient=None, name="settle.db"):
    factory = SqliteUnitOfWorkFactory(str(tmp_path / name))
    return SettlePayment(factory, recipient=recipient), factory


def seed(factory, wallet, *rows):
    """Put a wallet and its ledger rows in the store, in one unit.

    One unit rather than a commit per row, because a ledger row cannot exist
    without its wallet - ``transactions.wallet_id`` is a real foreign key and
    ``PRAGMA foreign_keys`` is on - so the two saves are one fact.
    """
    uow = factory.start()
    uow.wallets.save(wallet)
    for row in rows:
        uow.transactions.save(row)
    uow.commit()


def stored_wallet(factory, wallet_id, owner):
    uow = factory.start()
    try:
        return uow.wallets.get_owned(wallet_id, owner)
    finally:
        uow.rollback()


def available(factory, wallet):
    """The wallet as the *store* holds it, and only its available balance.

    The read goes through ``get_owned`` with the wallet's own owner, which is
    what every assertion about a balance in this file is really checking: that a
    settlement credited the wallet its row names, rather than some wallet the
    test happened to be holding a reference to.

    Shortened to one expression because it appears in almost every test, and a
    helper called ``available`` reads as the question being asked.
    """
    return stored_wallet(factory, wallet.wallet_id, wallet.user_id).available_balance


def stored_row(factory, provider_reference):
    uow = factory.start()
    try:
        return uow.transactions.get_by_provider_reference(provider_reference)
    finally:
        uow.rollback()


def pending_receipts(factory):
    uow = factory.start()
    try:
        return uow.notifications.pending()
    finally:
        uow.rollback()


def a_deposit(wallet, amount="5000", reference="dep-1"):
    """A deposit as ``InitiateDeposit`` leaves it: PENDING, provider-named.

    Note the wallet is *not* credited here, and that is the state the whole
    inbound slice turns on - a row that says money was asked for, and a balance
    that says none has arrived.
    """
    return Transaction(
        wallet_id=wallet.wallet_id,
        type=TransactionType.DEPOSIT,
        amount=Money(Decimal(amount), NGN),
        internal_reference=f"{wallet.wallet_id}.{reference}",
        provider_reference=reference,
    )


def a_transfer(
    wallet,
    type=TransactionType.PAYOUT,
    amount="5000",
    reference="pay-1",
    status=TransactionStatus.PENDING,
):
    """A withdrawal or payout row, in whichever state a test needs.

    The status is a parameter rather than a second helper because the two
    interesting states are the two ends of one movement: PENDING is a hold that
    has been taken and not yet resolved, and SUCCESSFUL is a hold that became a
    payment. A reversal is the only event that may touch the second.

    ``completed_at`` is set exactly when the row is finished, because
    ``Transaction.__post_init__`` refuses a SUCCESSFUL row without it - so a test
    that forgot would fail in the constructor rather than in an assertion about
    a reversal, which is the better place for it.

    The destination travels only on a PAYOUT, and that is the aggregate's rule
    rather than a detail of this helper: ``__post_init__`` refuses any other type
    that carries one, so a withdrawal with a bank account attached cannot be
    constructed at all. Both outbound types still settle identically, which is
    what ``_TRANSFER_TYPES`` says and what the withdrawal test below asserts.
    """
    finished = status is not TransactionStatus.PENDING
    return Transaction(
        wallet_id=wallet.wallet_id,
        type=type,
        amount=Money(Decimal(amount), NGN),
        internal_reference=f"{wallet.wallet_id}.{reference}",
        provider_reference=reference,
        destination=DESTINATION if type is TransactionType.PAYOUT else None,
        status=status,
        completed_at=datetime(2026, 1, 2) if finished else None,
    )


def settled(event, reference="pay-1", amount="5000"):
    """What the provider says happened, in our vocabulary rather than the wire's."""
    return ProviderOutcome(
        event=event, reference=reference, amount=Money(Decimal(amount), NGN)
    )


# --- the four movements -----------------------------------------------------


class TestAChargeCredits:
    def test_a_pending_deposit_is_credited_and_settled(self, tmp_path, build_wallet):
        wallet = build_wallet(available="1000")
        row = a_deposit(wallet)
        settler, factory = a_settler(tmp_path)
        seed(factory, wallet, row)

        result = settler.settle(settled(ProviderEvent.CHARGE_SUCCEEDED, "dep-1"))

        assert result.outcome is SettlementOutcome.DEPOSIT_CREDITED
        assert result.moved_money
        assert available(factory, wallet) == Money(6000, NGN)
        assert stored_row(factory, "dep-1").status is TransactionStatus.SUCCESSFUL

    def test_the_credit_is_the_amount_that_arrived(
        self, tmp_path, build_wallet
    ):
        """The event's amount, not the row's - which the check above makes equal.

        Worth pinning separately because the two are the same number here only
        because the mismatch rule refused everything else. A future change that
        credited the *row's* amount would pass every other test in this file and
        fail this one, which is exactly the shape of a regression worth a test of
        its own.
        """
        wallet = build_wallet(available="0")
        row = a_deposit(wallet, amount="9999.99")
        settler, factory = a_settler(tmp_path)
        seed(factory, wallet, row)

        settler.settle(settled(ProviderEvent.CHARGE_SUCCEEDED, "dep-1", "9999.99"))

        assert available(factory, wallet) == Money(Decimal("9999.99"), NGN)

    def test_a_settled_deposit_queues_a_receipt(self, tmp_path, build_wallet):
        """Decision 101 kept: the receipt a PENDING deposit was promised.

        ``WalletService._announce`` skips a PENDING row and its docstring promises
        that "the receipt arrives in the phase that settles these movements".
        This is that phase, and the kind is the one the CLI deposit has always
        used - there is no second deposit receipt, because there is no second
        kind of deposit.
        """
        wallet = build_wallet(available="1000")
        row = a_deposit(wallet)
        settler, factory = a_settler(tmp_path, recipient=RECIPIENT)
        seed(factory, wallet, row)

        settler.settle(settled(ProviderEvent.CHARGE_SUCCEEDED, "dep-1"))

        receipts = pending_receipts(factory)
        assert [one.kind for one in receipts] == [NotificationKind.WALLET_DEPOSIT]


class TestATransferThatLanded:
    def test_a_pending_payout_is_settled_and_no_balance_moves(
        self, tmp_path, build_wallet
    ):
        """The hold was taken at request time, so succeeding is only a status.

        The balance is asserted even though nothing should change it, because
        "nothing moved" is the claim - and a version of this that credited the
        money a second time would be a wallet that gained by sending money out.
        """
        wallet = build_wallet(available="5000")
        row = a_transfer(wallet)
        settler, factory = a_settler(tmp_path)
        seed(factory, wallet, row)

        result = settler.settle(settled(ProviderEvent.TRANSFER_SUCCEEDED))

        assert result.outcome is SettlementOutcome.TRANSFER_SETTLED
        assert result.moved_money
        assert available(factory, wallet) == Money(5000, NGN)
        assert stored_row(factory, "pay-1").status is TransactionStatus.SUCCESSFUL

    def test_a_withdrawal_settles_the_same_way(self, tmp_path, build_wallet):
        """Both outbound row types, because ``_TRANSFER_TYPES`` names two.

        A withdrawal and a payout are the same fact from the provider's side -
        money sent to a bank account - so a dispatch that handled one and not the
        other would be visible only here.
        """
        wallet = build_wallet(available="5000")
        row = a_transfer(wallet, type=TransactionType.WITHDRAWAL)
        settler, factory = a_settler(tmp_path)
        seed(factory, wallet, row)

        result = settler.settle(settled(ProviderEvent.TRANSFER_SUCCEEDED))

        assert result.outcome is SettlementOutcome.TRANSFER_SETTLED


class TestATransferThatFailed:
    def test_a_failed_payout_gives_the_hold_back(self, tmp_path, build_wallet):
        """**This is decision 106 closing**, and why ``release_hold`` exists."""
        wallet = build_wallet(available="5000")
        row = a_transfer(wallet, reference="pay-2")
        settler, factory = a_settler(tmp_path)
        seed(factory, wallet, row)

        result = settler.settle(settled(ProviderEvent.TRANSFER_FAILED, "pay-2"))

        assert result.outcome is SettlementOutcome.HOLD_RELEASED
        assert result.moved_money
        assert available(factory, wallet) == Money(10000, NGN)
        assert stored_row(factory, "pay-2").status is TransactionStatus.FAILED

    def test_a_failed_transfer_queues_no_receipt(self, tmp_path, build_wallet):
        """A receipt is past tense and specific, and this one would be a lie.

        ``compose.wallet_movement`` would happily write "5000.00 NGN was paid to
        Chinedu Okafor" for a transfer that never left. Silence is the honest
        answer, and the README carries the consequence - the owner is not told -
        as an open item rather than as an oversight. See
        ``SettlePayment._announce``.
        """
        wallet = build_wallet(available="5000")
        row = a_transfer(wallet)
        settler, factory = a_settler(tmp_path, recipient=RECIPIENT)
        seed(factory, wallet, row)

        settler.settle(settled(ProviderEvent.TRANSFER_FAILED))

        assert pending_receipts(factory) == []


class TestATransferThatCameBack:
    def test_a_settled_payout_is_reversed_and_the_money_returns(
        self, tmp_path, build_wallet
    ):
        """A reversal touches a row that *succeeded*, which is the difference.

        The row is seeded SUCCESSFUL with its ``completed_at`` set, because that
        is what a settled payout looks like - and ``reverse()`` refusing anything
        else is the aggregate's rule, asserted from the other side in
        ``TestTheRefusals`` below.
        """
        wallet = build_wallet(available="5000")
        row = a_transfer(wallet, status=TransactionStatus.SUCCESSFUL)
        settler, factory = a_settler(tmp_path)
        seed(factory, wallet, row)

        result = settler.settle(settled(ProviderEvent.TRANSFER_REVERSED))

        assert result.outcome is SettlementOutcome.PAYMENT_REVERSED
        assert result.moved_money
        assert available(factory, wallet) == Money(10000, NGN)

    def test_the_reversed_row_keeps_both_timestamps(self, tmp_path, build_wallet):
        """``__post_init__``'s date invariants, satisfied by the transition.

        A REVERSED row must carry ``completed_at`` *and* ``reversed_at`` - it was
        finished, and then it was undone. That is a property of the aggregate, and
        it is asserted here rather than trusted because this is the one path in
        the codebase that reaches ``reverse()`` from outside.
        """
        wallet = build_wallet(available="5000")
        row = a_transfer(wallet, status=TransactionStatus.SUCCESSFUL)
        settler, factory = a_settler(tmp_path)
        seed(factory, wallet, row)

        settler.settle(settled(ProviderEvent.TRANSFER_REVERSED))

        reversed_row = stored_row(factory, "pay-1")
        assert reversed_row.status is TransactionStatus.REVERSED
        assert reversed_row.completed_at is not None
        assert reversed_row.reversed_at is not None

    def test_a_reversal_queues_no_receipt(self, tmp_path, build_wallet):
        """The sharper version of the failure's silence.

        Here the receipt would be *true and no longer the whole truth* - the
        money was paid and then came back - which is worse than saying nothing,
        because the owner would keep a message that is now wrong.
        """
        wallet = build_wallet(available="5000")
        row = a_transfer(wallet, status=TransactionStatus.SUCCESSFUL)
        settler, factory = a_settler(tmp_path, recipient=RECIPIENT)
        seed(factory, wallet, row)

        settler.settle(settled(ProviderEvent.TRANSFER_REVERSED))

        assert pending_receipts(factory) == []


# --- the refusals -----------------------------------------------------------


class TestTheRefusals:
    """Each of these is a value, not an exception, and none of them moves money.

    The shape is the point: a webhook's honest answer to all of them is a 200
    with a report. See ``SettlementOutcome`` for why a separate error type would
    have forced the route to catch it and translate back.
    """

    def test_an_unknown_reference_settles_nothing(self, tmp_path, build_wallet):
        wallet = build_wallet(available="1000")
        settler, factory = a_settler(tmp_path)
        seed(factory, wallet)

        result = settler.settle(settled(ProviderEvent.CHARGE_SUCCEEDED, "nobody"))

        assert result.outcome is SettlementOutcome.UNKNOWN_REFERENCE
        assert not result.moved_money
        assert available(factory, wallet) == Money(1000, NGN)

    def test_a_disagreeing_amount_settles_nothing(self, tmp_path, build_wallet):
        """A partial payment is not a thing the checkout does, so this is a bug.

        The row is left PENDING rather than failed: nothing about it is wrong,
        and the reconciler (which does not exist yet) is what picks it up. Failing
        it here would throw away the record of what was asked for.
        """
        wallet = build_wallet(available="1000")
        row = a_deposit(wallet, amount="5000")
        settler, factory = a_settler(tmp_path)
        seed(factory, wallet, row)

        result = settler.settle(
            settled(ProviderEvent.CHARGE_SUCCEEDED, "dep-1", "4000")
        )

        assert result.outcome is SettlementOutcome.AMOUNT_DISAGREES
        assert available(factory, wallet) == Money(1000, NGN)
        assert stored_row(factory, "dep-1").status is TransactionStatus.PENDING

    def test_a_replayed_charge_settles_once(self, tmp_path, build_wallet):
        """**Idempotency, and why no table is needed for it.**

        The credit and the status change are written in one unit, so there is no
        instant at which the money has moved and the row still says PENDING - and
        a retry therefore cannot arrive in a window where it would apply twice.
        The second answer is the row's own status, read back.
        """
        wallet = build_wallet(available="1000")
        row = a_deposit(wallet)
        settler, factory = a_settler(tmp_path)
        seed(factory, wallet, row)
        charge = settled(ProviderEvent.CHARGE_SUCCEEDED, "dep-1")

        first = settler.settle(charge)
        second = settler.settle(charge)

        assert first.outcome is SettlementOutcome.DEPOSIT_CREDITED
        assert second.outcome is SettlementOutcome.ALREADY_SETTLED
        assert not second.moved_money
        assert available(factory, wallet) == Money(6000, NGN)

    def test_a_charge_cannot_settle_a_payout(self, tmp_path, build_wallet):
        wallet = build_wallet(available="5000")
        row = a_transfer(wallet)
        settler, factory = a_settler(tmp_path)
        seed(factory, wallet, row)

        result = settler.settle(settled(ProviderEvent.CHARGE_SUCCEEDED))

        assert result.outcome is SettlementOutcome.WRONG_KIND
        assert available(factory, wallet) == Money(5000, NGN)
        assert stored_row(factory, "pay-1").status is TransactionStatus.PENDING

    def test_a_transfer_cannot_settle_a_deposit(self, tmp_path, build_wallet):
        """The mirror of the test above, and it is not the same test twice.

        This is the one that would catch a dispatch written on the row's type
        instead of on the event's - a mistake that would look correct until a
        provider sent a transfer about a deposit.
        """
        wallet = build_wallet(available="1000")
        row = a_deposit(wallet)
        settler, factory = a_settler(tmp_path)
        seed(factory, wallet, row)

        result = settler.settle(settled(ProviderEvent.TRANSFER_FAILED, "dep-1"))

        assert result.outcome is SettlementOutcome.WRONG_KIND
        assert available(factory, wallet) == Money(1000, NGN)

    def test_a_reversal_needs_a_settled_transfer(self, tmp_path, build_wallet):
        """A reversal of a pending payout is not a reversal - it is a failure.

        The two events pay out the same, and only this distinguishes them: a
        transfer that never left is *failed*, and reversing it would be recording
        an undo of something that did not happen.
        """
        wallet = build_wallet(available="5000")
        row = a_transfer(wallet, status=TransactionStatus.PENDING)
        settler, factory = a_settler(tmp_path)
        seed(factory, wallet, row)

        result = settler.settle(settled(ProviderEvent.TRANSFER_REVERSED))

        assert result.outcome is SettlementOutcome.ALREADY_SETTLED
        assert available(factory, wallet) == Money(5000, NGN)
        assert stored_row(factory, "pay-1").status is TransactionStatus.PENDING

    def test_money_for_a_closed_wallet_is_refused_and_needs_a_person(
        self, tmp_path, build_wallet
    ):
        """The hole the plan did not cover, found while writing the use case.

        A wallet can be closed between the payer opening the authorization URL
        and the payer paying it - the money a pending payout holds is in neither
        the available balance nor a pot, so ``close()`` succeeds. When the charge
        then arrives, ``apply_deposit`` refuses the wallet, and the honest answer
        is neither a 500 (the provider would retry a payment that can never
        apply) nor a silent credit (there is nobody who can use the money). It is
        a named outcome, and the row is left PENDING so the money is still on the
        ledger for whoever settles it by hand.
        """
        wallet = build_wallet(available="1000", status=WalletStatus.CLOSED)
        row = a_deposit(wallet)
        settler, factory = a_settler(tmp_path)
        seed(factory, wallet, row)

        result = settler.settle(settled(ProviderEvent.CHARGE_SUCCEEDED, "dep-1"))

        assert result.outcome is SettlementOutcome.WALLET_CLOSED
        assert not result.moved_money
        assert available(factory, wallet) == Money(1000, NGN)
        assert stored_row(factory, "dep-1").status is TransactionStatus.PENDING


class TestWhoTheWalletBelongsTo:
    """The one privileged read in the codebase, asserted from its own side.

    ``SettlePayment`` finds the owner through ``WalletRepository.owner_of`` and
    then loads the wallet through ``get_owned`` - so the money is still only ever
    read as its owner's. What is worth pinning is that *nothing about a caller
    decides this*, because there is no caller.
    """

    def test_the_settled_wallet_may_belong_to_anybody(self, tmp_path, build_wallet):
        """A settlement works for a wallet owned by a user this test never names.

        Which is the whole reason the use case exists rather than a privileged
        mode on ``WalletService``: a provider's event is not scoped to a person,
        so a design that needed one would have had to invent it - and inventing
        one is exactly the bypass decision 54 removed.
        """
        stranger = uuid4()
        wallet = build_wallet(available="1000", user_id=stranger)
        row = a_deposit(wallet)
        settler, factory = a_settler(tmp_path)
        seed(factory, wallet, row)

        result = settler.settle(settled(ProviderEvent.CHARGE_SUCCEEDED, "dep-1"))

        assert result.outcome is SettlementOutcome.DEPOSIT_CREDITED
        assert available(factory, wallet) == Money(6000, NGN)
