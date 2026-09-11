from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.wallet_service import WalletService
from app.domain.money.currency import Currency
from app.domain.money.destination import Destination
from app.domain.money.destinationKind import DestinationKind
from app.domain.money.exception import (
    InsufficientFundsError,
    WalletAlreadyActiveError,
    WalletAlreadyFrozenError,
    WalletClosedError,
    WalletNotFoundError,
)
from app.domain.money.money import Money
from app.domain.money.transactionStatus import TransactionStatus
from app.domain.money.transactionType import TransactionType
from app.domain.money.wallet import Wallet
from app.domain.money.walletStatus import WalletStatus
from app.domain.notifications.notificationKind import NotificationKind
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)

NGN = Currency.NGN

#: Where a wallet receipt is addressed. Passed in rather than read from the
#: environment, because ``WalletService`` takes its inputs as arguments -
#: reading ``os.environ`` is the CLI's job, not the use case's.
RECIPIENT = "chinedu@example.com"

DESTINATION = Destination(
    kind=DestinationKind.BANK_ACCOUNT,
    identifier="0123456789",
    name="Chinedu Okafor",
    details={"bank_code": "058"},
)


def build_service(tmp_path, recipient=None, name="wallet_service.db"):
    factory = SqliteUnitOfWorkFactory(str(tmp_path / name))
    return WalletService(factory, recipient=recipient), factory


def seed(factory, wallet):
    uow = factory.start()
    uow.wallets.save(wallet)
    uow.commit()


def get_wallet(factory, wallet_id):
    uow = factory.start()
    try:
        return uow.wallets.get_by_id(wallet_id)
    finally:
        uow.rollback()


def get_transaction(factory, internal_reference):
    uow = factory.start()
    try:
        return uow.transactions.get_by_internal_reference(internal_reference)
    finally:
        uow.rollback()


def notifications_of(factory):
    """Every receipt the wallet operations have left owed - the queue, not the report."""
    uow = factory.start()
    try:
        return uow.notifications.pending()
    finally:
        uow.rollback()


def test_deposit_loads_wallet_and_persists_new_balance(tmp_path, build_wallet):
    wallet = build_wallet()
    service, factory = build_service(tmp_path)
    seed(factory, wallet)

    transaction = service.deposit(
        wallet.wallet_id,
        Money(Decimal("5000"), NGN),
        internal_reference=str(uuid4()),
    )

    assert transaction.status is TransactionStatus.SUCCESSFUL
    assert (
        get_wallet(factory, wallet.wallet_id).available_balance
        == Money(Decimal("15000"), NGN)
    )
    stored_transaction = get_transaction(factory, transaction.internal_reference)
    assert stored_transaction is not None
    assert stored_transaction.status is TransactionStatus.SUCCESSFUL


def test_withdrawal_persists_new_balance(tmp_path, build_wallet):
    wallet = build_wallet()
    service, factory = build_service(tmp_path)
    seed(factory, wallet)

    service.withdraw(
        wallet.wallet_id,
        Money(Decimal("3000"), NGN),
        internal_reference=str(uuid4()),
    )

    assert (
        get_wallet(factory, wallet.wallet_id).available_balance
        == Money(Decimal("7000"), NGN)
    )


def test_lock_moves_both_balances(tmp_path, build_wallet):
    wallet = build_wallet()
    service, factory = build_service(tmp_path)
    seed(factory, wallet)

    service.lock(
        wallet.wallet_id,
        Money(Decimal("3000"), NGN),
        internal_reference=str(uuid4()),
    )

    stored = get_wallet(factory, wallet.wallet_id)
    assert stored.available_balance == Money(Decimal("7000"), NGN)
    assert stored.locked_balance == Money(Decimal("3000"), NGN)


def test_payout_from_locked_spends_the_locked_balance(tmp_path, build_wallet):
    """Round-trips PAYOUT through SQLite, so the enum name persists and hydrates."""
    wallet = build_wallet(available="1000", locked="5000")
    service, factory = build_service(tmp_path)
    seed(factory, wallet)

    transaction = service.payout_from_locked(
        wallet.wallet_id,
        Money(Decimal("3000"), NGN),
        str(uuid4()),
        DESTINATION,
    )

    assert transaction.type is TransactionType.PAYOUT
    stored = get_wallet(factory, wallet.wallet_id)
    assert stored.locked_balance == Money(Decimal("2000"), NGN)
    assert stored.available_balance == Money(Decimal("1000"), NGN)


def test_payout_destination_survives_the_database(tmp_path, build_wallet):
    """The whole point of snapshotting: where the money went is recoverable."""
    wallet = build_wallet(locked="5000")
    service, factory = build_service(tmp_path)
    seed(factory, wallet)

    transaction = service.payout_from_locked(
        wallet.wallet_id,
        Money(Decimal("3000"), NGN),
        str(uuid4()),
        DESTINATION,
    )

    stored_transaction = get_transaction(factory, transaction.internal_reference)
    assert stored_transaction.destination == DESTINATION
    assert stored_transaction.destination.detail("bank_code") == "058"


def test_rejected_payout_persists_a_failed_audit_row_and_no_balance_change(tmp_path, build_wallet):
    wallet = build_wallet(locked="5000")
    service, factory = build_service(tmp_path)
    seed(factory, wallet)
    internal_reference = str(uuid4())

    with pytest.raises(InsufficientFundsError):
        service.payout_from_locked(
            wallet.wallet_id,
            Money(Decimal("15000"), NGN),
            internal_reference,
            DESTINATION,
        )

    assert (
        get_wallet(factory, wallet.wallet_id).locked_balance
        == Money(Decimal("5000"), NGN)
    )
    stored_transaction = get_transaction(factory, internal_reference)
    assert stored_transaction is not None
    assert stored_transaction.status is TransactionStatus.FAILED


def test_operation_on_unknown_wallet_raises(tmp_path):
    service, factory = build_service(tmp_path)

    with pytest.raises(WalletNotFoundError):
        service.deposit(
            uuid4(),
            Money(Decimal("5000"), NGN),
            internal_reference=str(uuid4()),
        )


def test_rejected_operation_persists_a_failed_audit_row_and_no_balance_change(tmp_path, build_wallet):
    wallet = build_wallet(status=WalletStatus.CLOSED)
    service, factory = build_service(tmp_path)
    seed(factory, wallet)
    internal_reference = str(uuid4())

    with pytest.raises(WalletClosedError):
        service.deposit(
            wallet.wallet_id,
            Money(Decimal("5000"), NGN),
            internal_reference=internal_reference,
        )

    assert (
        get_wallet(factory, wallet.wallet_id).available_balance
        == Money(Decimal("10000"), NGN)
    )
    stored_transaction = get_transaction(factory, internal_reference)
    assert stored_transaction is not None
    assert stored_transaction.status is TransactionStatus.FAILED


def test_replaying_an_internal_reference_does_not_double_credit(tmp_path, build_wallet):
    wallet = build_wallet()
    service, factory = build_service(tmp_path)
    seed(factory, wallet)
    internal_reference = str(uuid4())

    first = service.deposit(
        wallet.wallet_id,
        Money(Decimal("5000"), NGN),
        internal_reference=internal_reference,
    )
    second = service.deposit(
        wallet.wallet_id,
        Money(Decimal("5000"), NGN),
        internal_reference=internal_reference,
    )

    assert second.transaction_id == first.transaction_id
    assert second.status is TransactionStatus.SUCCESSFUL
    assert (
        get_wallet(factory, wallet.wallet_id).available_balance
        == Money(Decimal("15000"), NGN)
    )


def test_open_wallet_persists_an_empty_active_wallet(tmp_path):
    service, factory = build_service(tmp_path)
    user_id = uuid4()

    wallet = service.open_wallet(user_id, NGN)

    stored = get_wallet(factory, wallet.wallet_id)
    assert stored.user_id == user_id
    assert stored.status is WalletStatus.ACTIVE
    assert stored.currency is NGN
    assert stored.available_balance == Money(Decimal("0"), NGN)
    assert stored.locked_balance == Money(Decimal("0"), NGN)


def test_get_wallet_of_unknown_id_raises(tmp_path):
    service, _ = build_service(tmp_path)

    with pytest.raises(WalletNotFoundError):
        service.get_wallet(uuid4())


def test_freeze_persists_frozen_then_unfreeze_restores_active(tmp_path, build_wallet):
    wallet = build_wallet()
    service, factory = build_service(tmp_path)
    seed(factory, wallet)

    frozen = service.freeze_wallet(wallet.wallet_id)
    assert frozen.status is WalletStatus.FROZEN
    assert (
        get_wallet(factory, wallet.wallet_id).status
        is WalletStatus.FROZEN
    )

    active = service.unfreeze_wallet(wallet.wallet_id)
    assert active.status is WalletStatus.ACTIVE
    assert (
        get_wallet(factory, wallet.wallet_id).status
        is WalletStatus.ACTIVE
    )


def test_freezing_an_already_frozen_wallet_rejects(tmp_path, build_wallet):
    wallet = build_wallet()
    service, factory = build_service(tmp_path)
    seed(factory, wallet)

    service.freeze_wallet(wallet.wallet_id)

    with pytest.raises(WalletAlreadyFrozenError):
        service.freeze_wallet(wallet.wallet_id)

    # Unfreezing a frozen wallet is the intended path - it succeeds.
    assert (
        service.unfreeze_wallet(wallet.wallet_id).status
        is WalletStatus.ACTIVE
    )


def test_unfreezing_an_already_active_wallet_rejects(tmp_path, build_wallet):
    wallet = build_wallet()
    service, factory = build_service(tmp_path)
    seed(factory, wallet)

    with pytest.raises(WalletAlreadyActiveError):
        service.unfreeze_wallet(wallet.wallet_id)


def test_status_change_on_unknown_wallet_raises(tmp_path):
    service, _ = build_service(tmp_path)

    with pytest.raises(WalletNotFoundError):
        service.freeze_wallet(uuid4())


def test_transactions_for_wallet_returns_ledger_oldest_first(tmp_path, build_wallet):
    wallet = build_wallet()
    service, factory = build_service(tmp_path)
    seed(factory, wallet)

    service.deposit(
        wallet.wallet_id,
        Money(Decimal("5000"), NGN),
        internal_reference=str(uuid4()),
    )
    service.withdraw(
        wallet.wallet_id,
        Money(Decimal("2000"), NGN),
        internal_reference=str(uuid4()),
    )

    ledger = service.transactions_for_wallet(wallet.wallet_id)

    assert [transaction.type for transaction in ledger] == [
        TransactionType.DEPOSIT,
        TransactionType.WITHDRAWAL,
    ]
    assert all(
        transaction.status is TransactionStatus.SUCCESSFUL
        for transaction in ledger
    )


def test_transactions_for_wallet_ignores_other_wallets(tmp_path, build_wallet):
    wallet = build_wallet()
    other_wallet = build_wallet()
    service, factory = build_service(tmp_path)
    seed(factory, wallet)
    seed(factory, other_wallet)

    service.deposit(
        wallet.wallet_id,
        Money(Decimal("5000"), NGN),
        internal_reference=str(uuid4()),
    )
    service.deposit(
        other_wallet.wallet_id,
        Money(Decimal("7000"), NGN),
        internal_reference=str(uuid4()),
    )

    ledger = service.transactions_for_wallet(wallet.wallet_id)

    assert len(ledger) == 1
    assert ledger[0].amount == Money(Decimal("5000"), NGN)


def test_transactions_for_wallet_of_unknown_wallet_raises(tmp_path):
    service, _ = build_service(tmp_path)

    with pytest.raises(WalletNotFoundError):
        service.transactions_for_wallet(uuid4())


class TestTheReceiptForAWalletCommand:
    """Every wallet operation that crosses the wallet's boundary says so.

    The receipt is queued inside the operation's own transaction, so a command
    that moved money cannot commit without it - see ``WalletService``. "Says so"
    is deliberately not "sends": composing and queueing are writes, and the
    network call happens afterwards, outside the transaction, in the CLI.

    Which operations speak is one dict - ``WalletService.ANNOUNCED`` - and the
    two classes of test below are its two halves. Everything in it queues exactly
    one receipt; everything out of it queues none, and that silence is a decision
    rather than an oversight.
    """

    def test_a_deposit_queues_exactly_one_receipt(self, tmp_path, build_wallet):
        wallet = build_wallet()
        service, factory = build_service(tmp_path, recipient=RECIPIENT)
        seed(factory, wallet)

        transaction = service.deposit(
            wallet.wallet_id, Money(Decimal("5000"), NGN), str(uuid4())
        )

        queued = notifications_of(factory)
        assert len(queued) == 1
        assert queued[0].kind is NotificationKind.WALLET_DEPOSIT
        assert queued[0].subject_id == wallet.wallet_id
        assert queued[0].recipient == RECIPIENT
        assert queued[0].event_key == (
            f"wallet_deposit:wallet:{wallet.wallet_id}:"
            f"{transaction.internal_reference}"
        )

    def test_a_withdrawal_queues_exactly_one_receipt(self, tmp_path, build_wallet):
        wallet = build_wallet()
        service, factory = build_service(tmp_path, recipient=RECIPIENT)
        seed(factory, wallet)

        service.withdraw(
            wallet.wallet_id, Money(Decimal("3000"), NGN), str(uuid4())
        )

        queued = notifications_of(factory)
        assert len(queued) == 1
        assert queued[0].kind is NotificationKind.WALLET_WITHDRAWAL

    def test_a_payout_queues_exactly_one_receipt(self, tmp_path, build_wallet):
        wallet = build_wallet(locked="5000")
        service, factory = build_service(tmp_path, recipient=RECIPIENT)
        seed(factory, wallet)

        service.payout_from_locked(
            wallet.wallet_id,
            Money(Decimal("3000"), NGN),
            str(uuid4()),
            DESTINATION,
        )

        queued = notifications_of(factory)
        assert len(queued) == 1
        assert queued[0].kind is NotificationKind.WALLET_PAYOUT
        assert "Chinedu Okafor" in queued[0].body

    def test_the_receipt_reports_the_balance_the_command_left(
        self, tmp_path, build_wallet
    ):
        """Composed from the wallet *after* the operation ran, not before it.

        This is most of the value of the message: "5,000 arrived" is a fact, "you
        now hold 15,000" is the answer to the question the reader opened it with.
        Reading the balance before the money moved would get it exactly backwards
        and still look plausible.
        """
        wallet = build_wallet()
        service, factory = build_service(tmp_path, recipient=RECIPIENT)
        seed(factory, wallet)

        service.deposit(wallet.wallet_id, Money(Decimal("5000"), NGN), str(uuid4()))

        assert "available  15000.00 NGN" in notifications_of(factory)[0].body

    def test_a_lock_says_nothing(self, tmp_path, build_wallet):
        """The operation that is absent on purpose, and the reason it is absent.

        Locking moves money between the wallet's own two balances: nothing the
        owner holds changes, and they typed the command at a terminal that has
        already printed the result. A receipt for that is not information - it is
        a second acknowledgement of something the user did themselves.
        """
        wallet = build_wallet()
        service, factory = build_service(tmp_path, recipient=RECIPIENT)
        seed(factory, wallet)

        service.lock(wallet.wallet_id, Money(Decimal("3000"), NGN), str(uuid4()))

        assert notifications_of(factory) == []

    def test_a_release_says_nothing(self, tmp_path, build_wallet):
        wallet = build_wallet(locked="5000")
        service, factory = build_service(tmp_path, recipient=RECIPIENT)
        seed(factory, wallet)

        service.release(wallet.wallet_id, Money(Decimal("2000"), NGN), str(uuid4()))

        assert notifications_of(factory) == []

    def test_a_rejected_operation_says_nothing(self, tmp_path, build_wallet):
        """An error is already the message - it is printed where it happened.

        The rejection path commits a FAILED audit row and re-raises, and the
        receipt is queued after the operation *returns*, so a refusal never
        reaches it. That is the right outcome: the user is looking at the
        terminal, and the failure is the thing they need to read.
        """
        wallet = build_wallet(status=WalletStatus.CLOSED)
        service, factory = build_service(tmp_path, recipient=RECIPIENT)
        seed(factory, wallet)

        with pytest.raises(WalletClosedError):
            service.deposit(
                wallet.wallet_id, Money(Decimal("5000"), NGN), str(uuid4())
            )

        assert notifications_of(factory) == []

    def test_with_no_recipient_the_command_happens_and_says_nothing(
        self, tmp_path, build_wallet
    ):
        """The ordinary state of a fresh install, and never a reason to refuse.

        A missing mail address is a reason to be quiet about a payment, not a
        reason to decline one - so the money moves exactly as it otherwise would.
        """
        wallet = build_wallet()
        service, factory = build_service(tmp_path, recipient=None)
        seed(factory, wallet)

        service.deposit(wallet.wallet_id, Money(Decimal("5000"), NGN), str(uuid4()))

        assert notifications_of(factory) == []
        assert (
            get_wallet(factory, wallet.wallet_id).available_balance
            == Money(Decimal("15000"), NGN)
        )

    def test_a_repeated_deposit_is_announced_once(self, tmp_path, build_wallet):
        """The idempotent path, and the reason the message is built from the row.

        Replaying a reference returns the *existing* ledger row and moves no
        money - so the second call composes a receipt for an event that already
        has one. It derives the same key, the insert claims nothing, and one
        deposit stays one email with no check anywhere. See decision 31.
        """
        wallet = build_wallet()
        service, factory = build_service(tmp_path, recipient=RECIPIENT)
        seed(factory, wallet)
        reference = str(uuid4())

        first = service.deposit(
            wallet.wallet_id, Money(Decimal("5000"), NGN), reference
        )
        second = service.deposit(
            wallet.wallet_id, Money(Decimal("5000"), NGN), reference
        )

        assert second.transaction_id == first.transaction_id
        queued = notifications_of(factory)
        assert len(queued) == 1
        assert queued[0].event_key.endswith(reference)

    def test_two_deposits_are_two_receipts(self, tmp_path, build_wallet):
        """The other half of the dedupe: a genuinely different deposit still speaks."""
        wallet = build_wallet()
        service, factory = build_service(tmp_path, recipient=RECIPIENT)
        seed(factory, wallet)

        service.deposit(wallet.wallet_id, Money(Decimal("5000"), NGN), str(uuid4()))
        service.deposit(wallet.wallet_id, Money(Decimal("1000"), NGN), str(uuid4()))

        assert len(notifications_of(factory)) == 2

