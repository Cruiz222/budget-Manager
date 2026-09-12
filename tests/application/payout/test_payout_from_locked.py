from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.payout.payout_from_locked import PayoutFromLocked
from app.domain.money.currency import Currency
from app.domain.money.destination import Destination
from app.domain.money.destinationKind import DestinationKind
from app.domain.money.exception import (
    CurrencyMismatchError,
    InsufficientFundsError,
    InvalidAmountError,
    MissingDestinationError,
    WalletClosedError,
    WalletFrozenError,
)
from app.domain.money.fundKind import FundKind
from app.domain.money.money import Money
from app.domain.money.transactionStatus import TransactionStatus
from app.domain.money.transactionType import TransactionType
from app.domain.money.walletStatus import WalletStatus
from app.infrastructure.repositories.in_memory_transaction_repository import (
    InMemoryTransactionRepository,
)

NGN = Currency.NGN
USD = Currency.USD

#: Any moment after the fixture's pot. The fixture's pot is opened with no
#: maturity date - the state the migration puts every pre-existing locked balance
#: in - so it is open at every moment, and this one is chosen to *not* be "now".
#: No test in this file reads a clock.
MOMENT = datetime(2026, 1, 1)

DESTINATION = Destination(
    kind=DestinationKind.BANK_ACCOUNT,
    identifier="0123456789",
    name="Chinedu Okafor",
    details={"bank_code": "058"},
)


# --- A payout debits the pot and holds the money ---

def test_a_payout_spends_locked_and_leaves_the_row_pending(build_wallet):
    """It was ``test_successful_payout_...`` until Phase 2b, and the rename is
    the change rather than tidying around it.

    What the pot does is unambiguous and this file still asserts it: 3000 leaves
    the locked balance and the pot is 2000 lighter, so the money is out of
    reach - a second payout of the same amount is refused against what is left.
    What is *not* unambiguous is where the 3000 went. It is bound for a bank
    account, and nothing in this process has spoken to a bank. So the row says
    PENDING and carries no ``completed_at``, because the only party who could
    supply one is the one that has not answered yet.

    ``completed_at is None`` is not a detail of the row's construction - the
    domain refuses a PENDING transaction a completion moment outright, so this
    line fails if the row was built wrongly rather than merely left unfinished.
    """
    wallet = build_wallet(available="1000", locked="5000")
    repository = InMemoryTransactionRepository()

    transaction = PayoutFromLocked(wallet, repository, MOMENT).execute(
        Money(Decimal("3000"), NGN),
        internal_reference=str(uuid4()),
        destination=DESTINATION,
    )

    assert wallet.locked_balance == Money(Decimal("2000"), NGN)
    assert wallet.available_balance == Money(Decimal("1000"), NGN)

    stored = repository.get_by_id(transaction.transaction_id)
    assert stored.status is TransactionStatus.PENDING
    assert stored.wallet_id == wallet.wallet_id
    assert stored.type is TransactionType.PAYOUT
    assert stored.completed_at is None


def test_the_destination_is_recorded_on_the_ledger_entry(build_wallet):
    wallet = build_wallet(locked="5000")
    repository = InMemoryTransactionRepository()

    transaction = PayoutFromLocked(wallet, repository, MOMENT).execute(
        Money(Decimal("3000"), NGN),
        internal_reference=str(uuid4()),
        destination=DESTINATION,
    )

    stored = repository.get_by_id(transaction.transaction_id)
    assert stored.destination == DESTINATION


def test_payout_is_persisted_as_pending_before_wallet_is_touched(
    build_wallet, recording_transactions
):
    """The name still holds and only the expectation moved.

    A payout is genuinely written to the ledger as PENDING *before* the pot is
    debited, and that ordering is the whole point of the step: a crash between
    the two leaves a record of an attempt rather than a pot that lost money with
    nothing to explain it. What changed in 2b is the second save. There used to
    be one - the row rewritten SUCCESSFUL once the wallet had moved - and now
    there is nothing further to write, because a payout has no state this system
    can reach on its own. That is the pending intent visible in a single line.

    One asymmetry worth noticing: a *rejected* payout still saves twice
    (``test_rejected_payout_is_persisted_as_pending_then_failed`` below). Failure
    is a fact this system can establish by itself, so a refusal does reach a
    conclusion - it just is not the one the money is waiting for.
    """
    wallet = build_wallet(locked="5000")

    PayoutFromLocked(wallet, recording_transactions, MOMENT).execute(
        Money(Decimal("3000"), NGN),
        internal_reference=str(uuid4()),
        destination=DESTINATION,
    )

    assert recording_transactions.saved_statuses == [TransactionStatus.PENDING]


# --- A payout must say where the money went ---

def test_payout_without_a_destination_is_rejected_and_persists_nothing(build_wallet):
    """The rule lives on the Transaction, so it fires before anything is saved."""
    wallet = build_wallet(locked="5000")
    repository = InMemoryTransactionRepository()

    with pytest.raises(MissingDestinationError):
        PayoutFromLocked(wallet, repository, MOMENT).execute(
            Money(Decimal("3000"), NGN),
            internal_reference=str(uuid4()),
        )

    assert wallet.locked_balance == Money(Decimal("5000"), NGN)
    assert not repository.transactions


# --- Invalid amounts are rejected before any record exists ---

def test_payout_with_zero_amount_fails_and_persists_nothing(build_wallet):
    wallet = build_wallet(locked="5000")
    repository = InMemoryTransactionRepository()

    with pytest.raises(InvalidAmountError):
        PayoutFromLocked(wallet, repository, MOMENT).execute(
            Money(Decimal("0"), NGN),
            internal_reference=str(uuid4()),
            destination=DESTINATION,
        )

    assert wallet.locked_balance == Money(Decimal("5000"), NGN)
    assert not repository.transactions


def test_payout_with_negative_amount_fails_and_persists_nothing(build_wallet):
    wallet = build_wallet(locked="5000")
    repository = InMemoryTransactionRepository()

    with pytest.raises(InvalidAmountError):
        PayoutFromLocked(wallet, repository, MOMENT).execute(
            Money(Decimal("-3000"), NGN),
            internal_reference=str(uuid4()),
            destination=DESTINATION,
        )

    assert wallet.locked_balance == Money(Decimal("5000"), NGN)
    assert not repository.transactions


def test_payout_with_non_money_amount_fails_and_persists_nothing(build_wallet):
    wallet = build_wallet(locked="5000")
    repository = InMemoryTransactionRepository()

    with pytest.raises(InvalidAmountError):
        PayoutFromLocked(wallet, repository, MOMENT).execute(
            3000,
            internal_reference=str(uuid4()),
            destination=DESTINATION,
        )

    assert not repository.transactions


# --- Wallet rejections leave a FAILED audit record ---

def test_payout_more_than_locked_balance_fails_and_persists_failed_transaction(build_wallet):
    wallet = build_wallet(available="10000", locked="5000")
    repository = InMemoryTransactionRepository()

    with pytest.raises(InsufficientFundsError):
        PayoutFromLocked(wallet, repository, MOMENT).execute(
            Money(Decimal("15000"), NGN),
            internal_reference=str(uuid4()),
            destination=DESTINATION,
        )

    assert wallet.locked_balance == Money(Decimal("5000"), NGN)
    assert wallet.available_balance == Money(Decimal("10000"), NGN)

    stored = list(repository.transactions.values())[0]
    assert stored.status is TransactionStatus.FAILED


def test_payout_from_closed_wallet_fails_and_persists_failed_transaction(build_wallet):
    wallet = build_wallet(status=WalletStatus.CLOSED, locked="5000")
    repository = InMemoryTransactionRepository()

    with pytest.raises(WalletClosedError):
        PayoutFromLocked(wallet, repository, MOMENT).execute(
            Money(Decimal("3000"), NGN),
            internal_reference=str(uuid4()),
            destination=DESTINATION,
        )

    assert wallet.locked_balance == Money(Decimal("5000"), NGN)

    stored = list(repository.transactions.values())[0]
    assert stored.status is TransactionStatus.FAILED


def test_payout_from_frozen_wallet_fails_and_persists_failed_transaction(build_wallet):
    wallet = build_wallet(status=WalletStatus.FROZEN, locked="5000")
    repository = InMemoryTransactionRepository()

    with pytest.raises(WalletFrozenError):
        PayoutFromLocked(wallet, repository, MOMENT).execute(
            Money(Decimal("3000"), NGN),
            internal_reference=str(uuid4()),
            destination=DESTINATION,
        )

    assert wallet.locked_balance == Money(Decimal("5000"), NGN)

    stored = list(repository.transactions.values())[0]
    assert stored.status is TransactionStatus.FAILED


def test_payout_with_wrong_currency_fails_and_persists_failed_transaction(build_wallet):
    wallet = build_wallet(locked="5000")
    repository = InMemoryTransactionRepository()

    with pytest.raises(CurrencyMismatchError):
        PayoutFromLocked(wallet, repository, MOMENT).execute(
            Money(Decimal("3000"), USD),
            internal_reference=str(uuid4()),
            destination=DESTINATION,
        )

    assert wallet.locked_balance == Money(Decimal("5000"), NGN)

    stored = list(repository.transactions.values())[0]
    assert stored.status is TransactionStatus.FAILED


def test_rejected_payout_is_persisted_as_pending_then_failed(
    build_wallet, recording_transactions
):
    wallet = build_wallet(locked="5000")

    with pytest.raises(InsufficientFundsError):
        PayoutFromLocked(wallet, recording_transactions, MOMENT).execute(
            Money(Decimal("15000"), NGN),
            internal_reference=str(uuid4()),
            destination=DESTINATION,
        )

    assert recording_transactions.saved_statuses == [
        TransactionStatus.PENDING,
        TransactionStatus.FAILED,
    ]


# --- Idempotency ---

def test_replaying_the_same_internal_reference_pays_only_once(build_wallet):
    wallet = build_wallet(locked="5000")
    repository = InMemoryTransactionRepository()
    service = PayoutFromLocked(wallet, repository, MOMENT)
    reference = str(uuid4())

    first = service.execute(
        Money(Decimal("3000"), NGN), reference, destination=DESTINATION
    )
    second = service.execute(
        Money(Decimal("3000"), NGN), reference, destination=DESTINATION
    )

    assert second.transaction_id == first.transaction_id
    assert wallet.locked_balance == Money(Decimal("2000"), NGN)


def test_caller_supplied_internal_reference_is_persisted(build_wallet):
    wallet = build_wallet(locked="5000")
    repository = InMemoryTransactionRepository()
    reference = "salary-run-sep-005"

    transaction = PayoutFromLocked(wallet, repository, MOMENT).execute(
        Money(Decimal("3000"), NGN),
        internal_reference=reference,
        destination=DESTINATION,
    )

    stored = repository.get_by_id(transaction.transaction_id)
    assert stored.internal_reference == reference


# --- The moment the operation carries ---

def test_a_payout_cannot_spend_a_pot_that_has_not_come_due(build_wallet):
    """``as_of`` reaches the wallet, and this is how that is observable.

    The wallet holds 5000 and the payout asks for 1000, so nothing here is about
    the amount - only about the clock the operation was handed. The refusal is
    recorded as FAILED like every other rejection the wallet makes.
    """
    wallet = build_wallet(available="0")
    sealed = wallet.open_fund(
        "Vacation", FundKind.PERSONAL, maturity_date=date(2026, 6, 1)
    )
    wallet.deposit_into_fund(sealed.fund_id, Money(Decimal("5000"), NGN), MOMENT)
    repository = InMemoryTransactionRepository()

    with pytest.raises(InsufficientFundsError):
        PayoutFromLocked(
            wallet, repository, datetime(2026, 5, 31, 23, 59)
        ).execute(
            Money(Decimal("1000"), NGN),
            internal_reference=str(uuid4()),
            destination=DESTINATION,
        )

    assert sealed.balance == Money(Decimal("5000"), NGN)
    assert list(repository.transactions.values())[0].status is TransactionStatus.FAILED


def test_the_moment_is_the_operations_only_not_the_wallets(build_wallet):
    """Two operations over one wallet, handed different moments, disagree.

    The point being made is that ``as_of`` is a *parameter* and not a property
    of the wallet: the same sealed pot refuses one call and pays the next, and
    nothing about the wallet changed in between except the moment it was told
    about. If the wallet read ``datetime.now()`` for itself, this test could not
    be written at all.
    """
    wallet = build_wallet(available="0")
    sealed = wallet.open_fund(
        "Vacation", FundKind.PERSONAL, maturity_date=date(2026, 6, 1)
    )
    wallet.deposit_into_fund(sealed.fund_id, Money(Decimal("5000"), NGN), MOMENT)

    with pytest.raises(InsufficientFundsError):
        PayoutFromLocked(
            wallet, InMemoryTransactionRepository(), datetime(2026, 1, 1)
        ).execute(
            Money(Decimal("1000"), NGN),
            internal_reference="before",
            destination=DESTINATION,
        )

    PayoutFromLocked(
        wallet, InMemoryTransactionRepository(), datetime(2026, 6, 1, 9, 0)
    ).execute(
        Money(Decimal("1000"), NGN),
        internal_reference="on-the-day",
        destination=DESTINATION,
    )

    assert sealed.balance == Money(Decimal("4000"), NGN)


def test_a_payout_records_no_pot(build_wallet):
    """The ledger row is unchanged from before funds existed.

    A payout in this phase pays from the pool of matured pots and cannot say how
    the amount was split, so it says nothing rather than something wrong. The
    next phase names the pot on the plan, and starts writing this column.
    """
    wallet = build_wallet(locked="5000")

    transaction = PayoutFromLocked(
        wallet, InMemoryTransactionRepository(), MOMENT
    ).execute(
        Money(Decimal("3000"), NGN),
        internal_reference=str(uuid4()),
        destination=DESTINATION,
    )

    assert transaction.fund_id is None
