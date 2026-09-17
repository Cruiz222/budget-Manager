from uuid import uuid4
import pytest
from datetime import datetime
from decimal import Decimal
from app.domain.money.destination import Destination
from app.domain.money.destinationKind import DestinationKind
from app.domain.money.transactionStatus import TransactionStatus
from app.domain.money.transactionType import TransactionType
from app.domain.money.transaction import Transaction
from app.domain.money.currency import Currency
from app.domain.money.money import Money
from app.infrastructure.repositories.in_memory_transaction_repository import InMemoryTransactionRepository
from app.domain.money.exception import (
    TransactionNotFoundError
)


def test_save_in_memory_transaction_():
    transaction = Transaction (
        transaction_id=uuid4(),
        wallet_id=uuid4(),
        type=TransactionType.DEPOSIT,
        amount=Money(5000, Currency.NGN),
        internal_reference="internal_reference",
        provider_reference="provider_reference",
        metadata={},
        created_at=datetime.now(),

    )

    repository = InMemoryTransactionRepository()

    result = repository.save(transaction)

    assert result == transaction

    assert repository.transactions[transaction.transaction_id] == transaction


def test_get_by_id_return_same_transaction():
    transaction = Transaction (
        transaction_id=uuid4(),
        wallet_id=uuid4(),
        type=TransactionType.DEPOSIT,
        amount=Money(5000, Currency.NGN),
        internal_reference="internal_reference",
        provider_reference="provider_reference",
        metadata={},
        created_at=datetime.now(),

    )

    repository = InMemoryTransactionRepository()

    repository.save(transaction)

    result = repository.get_by_id(transaction.transaction_id)

    assert result == transaction



def test_get_by_invalid_id_raises_error():
   repository = InMemoryTransactionRepository()

   transaction_id = uuid4()

   with pytest.raises(TransactionNotFoundError):
    repository.get_by_id(transaction_id)


def test_get_by_internal_reference_returns_saved_transaction():
    transaction = Transaction(
        wallet_id=uuid4(),
        type=TransactionType.DEPOSIT,
        amount=Money(5000, Currency.NGN),
        internal_reference="payment-1",
    )

    repository = InMemoryTransactionRepository()

    repository.save(transaction)

    result = repository.get_by_internal_reference("payment-1")

    assert result is transaction


def test_get_by_internal_reference_returns_none_when_not_found():
    repository = InMemoryTransactionRepository()

    result = repository.get_by_internal_reference("unknown-reference")

    assert result is None


def test_get_by_wallet_id_returns_that_wallets_ledger_oldest_first():
    wallet_id = uuid4()
    other_wallet_id = uuid4()

    def build_transaction(owner, created_at):
        return Transaction(
            wallet_id=owner,
            type=TransactionType.DEPOSIT,
            amount=Money(5000, Currency.NGN),
            internal_reference=str(uuid4()),
            created_at=created_at,
        )

    repository = InMemoryTransactionRepository()
    first = build_transaction(wallet_id, datetime(2026, 1, 2))
    second = build_transaction(wallet_id, datetime(2026, 1, 3))
    stranger = build_transaction(other_wallet_id, datetime(2026, 1, 1))
    # Save out of order: the ledger must come back oldest first regardless.
    for transaction in (stranger, second, first):
        repository.save(transaction)

    ledger = repository.get_by_wallet_id(wallet_id)

    assert [t.transaction_id for t in ledger] == [
        first.transaction_id,
        second.transaction_id,
    ]


def test_get_by_wallet_id_is_empty_for_a_wallet_without_transactions():
    repository = InMemoryTransactionRepository()

    assert repository.get_by_wallet_id(uuid4()) == []


def test_get_by_provider_reference_finds_a_saved_transaction():
    repository = InMemoryTransactionRepository()
    transaction = Transaction(
        wallet_id=uuid4(),
        type=TransactionType.DEPOSIT,
        amount=Money(5000, Currency.NGN),
        internal_reference="payment-1",
        provider_reference="prov-123",
    )
    repository.save(transaction)

    result = repository.get_by_provider_reference("prov-123")

    assert result is transaction


def test_get_by_provider_reference_returns_none_when_not_found():
    repository = InMemoryTransactionRepository()

    result = repository.get_by_provider_reference("prov-missing")

    assert result is None


# --- the reconciliation read ------------------------------------------------


def test_list_awaiting_provider_returns_pending_rows_that_carry_a_reference():
    """**Both halves of the filter, and neither is decorative.**

    The reconciler may only ask a provider about rows a provider was actually
    told about, so the read is PENDING **and** provider-backed: the status half
    excludes a finished deposit, and the reference half excludes every CLI
    withdrawal and plan-run payout, which carry no reference because nothing
    outside ``InitiateDeposit`` ever writes one.
    """
    repository = InMemoryTransactionRepository()

    def build_transaction(created_at, provider_reference=None):
        return Transaction(
            wallet_id=uuid4(),
            type=TransactionType.DEPOSIT,
            amount=Money(5000, Currency.NGN),
            internal_reference=str(uuid4()),
            provider_reference=provider_reference,
            created_at=created_at,
        )

    awaiting = build_transaction(datetime(2026, 1, 2), "prov-awaiting")
    finished = build_transaction(datetime(2026, 1, 2), "prov-finished")
    finished.mark_successful()
    unsponsored = build_transaction(datetime(2026, 1, 2))
    for transaction in (awaiting, finished, unsponsored):
        repository.save(transaction)

    found = repository.list_awaiting_provider()

    assert [t.transaction_id for t in found] == [awaiting.transaction_id]


def test_list_awaiting_provider_is_oldest_first():
    """The order is the contract, because the batch takes from the front.

    Saved out of order, so a repository that returned insertion order would fail
    here - which is the same assertion ``get_by_wallet_id``'s ordering test makes
    one method over, and it is the property that stops the oldest stuck payment
    from being starved by a backlog longer than one batch.
    """
    repository = InMemoryTransactionRepository()

    def build_transaction(created_at):
        return Transaction(
            wallet_id=uuid4(),
            type=TransactionType.DEPOSIT,
            amount=Money(5000, Currency.NGN),
            internal_reference=str(uuid4()),
            provider_reference=f"prov-{created_at.hour}",
            created_at=created_at,
        )

    oldest = build_transaction(datetime(2026, 1, 2, 11))
    middle = build_transaction(datetime(2026, 1, 2, 12))
    newest = build_transaction(datetime(2026, 1, 2, 13))
    for transaction in (newest, oldest, middle):
        repository.save(transaction)

    found = repository.list_awaiting_provider()

    assert [t.transaction_id for t in found] == [
        oldest.transaction_id,
        middle.transaction_id,
        newest.transaction_id,
    ]


def test_list_awaiting_provider_is_empty_when_nothing_is_in_flight():
    repository = InMemoryTransactionRepository()

    assert repository.list_awaiting_provider() == []


# --- what left the wallet today --------------------------------------------
#
# The mirror of the section in ``test_sqlite_transaction_repository.py``. Two
# stores, one rule, and the rule is worth stating twice rather than sharing a
# helper: what makes these tests worth having is that each is written against its
# own store, so an implementation that drifted from the port fails here instead of
# passing on the strength of the other one.

DAY_START = datetime(2026, 3, 2)
DAY_END = datetime(2026, 3, 3)
MIDDAY = datetime(2026, 3, 2, 12, 0)

PAYOUT_DESTINATION = Destination(
    kind=DestinationKind.BANK_ACCOUNT,
    identifier="0123456789",
    name="Chinedu Okafor",
    details={"bank_code": "058"},
)


def build_outflow(wallet_id, **overrides):
    """A movement of value out of ``wallet_id``, at ``MIDDAY`` by default."""
    kwargs = dict(
        wallet_id=wallet_id,
        type=TransactionType.WITHDRAWAL,
        amount=Money(Decimal("1000.00"), Currency.NGN),
        internal_reference=str(uuid4()),
        created_at=MIDDAY,
    )
    kwargs.update(overrides)
    return Transaction(**kwargs)


def outflow(repository, wallet_id, currency=Currency.NGN):
    """That wallet's outflow over the day, with the window filled in."""
    return repository.outflow_total_between(
        wallet_id, DAY_START, DAY_END, currency
    )


def test_outflow_of_an_empty_ledger_is_zero_in_the_wallets_currency():
    """Zero rather than ``None``, carrying the currency it was asked about."""
    repository = InMemoryTransactionRepository()

    total = outflow(repository, uuid4())

    assert total == Money(0, Currency.NGN)
    assert total.currency is Currency.NGN


def test_outflow_counts_a_settled_withdrawal_and_a_held_payout():
    """The two statuses whose money is not in the wallet, added together."""
    wallet_id = uuid4()
    repository = InMemoryTransactionRepository()
    withdrawal = build_outflow(wallet_id, amount=Money(Decimal("250.25"), Currency.NGN))
    withdrawal.mark_successful()
    payout = build_outflow(
        wallet_id,
        type=TransactionType.PAYOUT,
        amount=Money(Decimal("749.75"), Currency.NGN),
        destination=PAYOUT_DESTINATION,
    )
    for transaction in (withdrawal, payout):
        repository.save(transaction)

    assert payout.status is TransactionStatus.PENDING
    assert outflow(repository, wallet_id) == Money(Decimal("1000.00"), Currency.NGN)


@pytest.mark.parametrize(
    ("transaction_type", "settle"),
    [
        # Never left the wallet: given back by the bank, or never sent at all.
        (TransactionType.WITHDRAWAL, "failed"),
        # Left and came back, which is the judgement call the port argues.
        (TransactionType.WITHDRAWAL, "reversed"),
        # Arrived, so it spends nothing.
        (TransactionType.DEPOSIT, "successful"),
        # Moved between the wallet's own two balances.
        (TransactionType.LOCK_FUNDS, "successful"),
        (TransactionType.UNLOCK_FUNDS, "successful"),
    ],
)
def test_outflow_excludes_every_row_whose_money_is_in_the_wallet(
    transaction_type, settle
):
    wallet_id = uuid4()
    repository = InMemoryTransactionRepository()
    transaction = build_outflow(wallet_id, type=transaction_type)
    if settle == "successful":
        transaction.mark_successful()
    elif settle == "failed":
        transaction.mark_failed()
    else:
        transaction.mark_successful()
        transaction.reverse()
    repository.save(transaction)

    assert outflow(repository, wallet_id) == Money(0, Currency.NGN)


def test_outflow_window_is_half_open():
    """``start`` inclusive, ``end`` exclusive - so the two days partition time.

    The row stamped exactly at midnight belongs to the day that is beginning, so
    asking the day before gives nothing while the day after gives it back.
    """
    wallet_id = uuid4()
    repository = InMemoryTransactionRepository()
    at_midnight = build_outflow(wallet_id, created_at=DAY_END)
    at_midnight.mark_successful()
    before_midnight = build_outflow(wallet_id, created_at=DAY_START)
    before_midnight.mark_successful()
    for transaction in (at_midnight, before_midnight):
        repository.save(transaction)

    assert outflow(repository, wallet_id) == Money(Decimal("1000.00"), Currency.NGN)
    assert repository.outflow_total_between(
        wallet_id, DAY_END, datetime(2026, 3, 4), Currency.NGN
    ) == Money(Decimal("1000.00"), Currency.NGN)


def test_outflow_is_one_wallets_and_one_currencys():
    wallet_id = uuid4()
    repository = InMemoryTransactionRepository()
    mine = build_outflow(wallet_id, amount=Money(Decimal("300.00"), Currency.NGN))
    theirs = build_outflow(uuid4(), amount=Money(Decimal("9000.00"), Currency.NGN))
    foreign = build_outflow(
        wallet_id,
        # No ``currency`` argument, because a Transaction has no such parameter:
        # its currency *is* the currency of its amount, and there is no second
        # field that could disagree with it. That is the property the assertion
        # below leans on - the filter cannot be handed a currency the row does not
        # carry.
        amount=Money(Decimal("500.00"), Currency.USD),
    )
    for transaction in (mine, theirs, foreign):
        transaction.mark_successful()
        repository.save(transaction)

    assert outflow(repository, wallet_id) == Money(Decimal("300.00"), Currency.NGN)
    assert outflow(repository, wallet_id, Currency.USD) == Money(
        Decimal("500.00"), Currency.USD
    )

