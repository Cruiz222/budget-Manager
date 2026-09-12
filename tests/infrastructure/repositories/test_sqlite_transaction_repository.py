from datetime import datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.domain.money.currency import Currency
from app.domain.money.destination import Destination
from app.domain.money.destinationKind import DestinationKind
from app.domain.money.exception import TransactionNotFoundError
from app.domain.money.money import Money
from app.domain.money.transaction import Transaction
from app.domain.money.transactionStatus import TransactionStatus
from app.domain.money.transactionType import TransactionType
from app.domain.money.wallet import Wallet
from app.domain.money.walletStatus import WalletStatus
from app.infrastructure.persistence.sqlite_unit_of_work import open_sqlite_connection
from app.infrastructure.repositories.sqlite_transaction_repository import (
    SqliteTransactionRepository,
)
from app.infrastructure.repositories.sqlite_wallet_repository import (
    SqliteWalletRepository,
)

NGN = Currency.NGN

DESTINATION = Destination(
    kind=DestinationKind.BANK_ACCOUNT,
    identifier="0123456789",
    name="Chinedu Okafor",
    details={"bank_code": "058"},
)


def build_transaction(wallet, **overrides):
    kwargs = dict(
        wallet_id=wallet.wallet_id,
        type=TransactionType.DEPOSIT,
        amount=Money(Decimal("5000"), NGN),
        internal_reference=str(uuid4()),
    )
    kwargs.update(overrides)
    return Transaction(**kwargs)


def build_repository(wallet):
    connection = open_sqlite_connection(":memory:")
    # Seed the wallet row so the transaction's foreign key resolves.
    SqliteWalletRepository(connection).save(wallet)
    return SqliteTransactionRepository(connection)


def test_round_trips_a_successful_transaction(build_wallet):
    wallet = build_wallet()
    transaction = build_transaction(
        wallet,
        provider_reference="prov-1",
        narration="first deposit",
        metadata={"channel": "web"},
    )
    transaction.mark_successful()
    repository = build_repository(wallet)

    repository.save(transaction)

    stored = repository.get_by_id(transaction.transaction_id)
    assert stored.transaction_id == transaction.transaction_id
    assert stored.wallet_id == wallet.wallet_id
    assert stored.type is TransactionType.DEPOSIT
    assert stored.status is TransactionStatus.SUCCESSFUL
    assert stored.amount == Money(Decimal("5000"), NGN)
    assert stored.internal_reference == transaction.internal_reference
    assert stored.provider_reference == "prov-1"
    assert stored.narration == "first deposit"
    assert dict(stored.metadata) == {"channel": "web"}
    assert stored.created_at == transaction.created_at
    assert stored.completed_at == transaction.completed_at


def test_save_updates_the_same_row_across_the_lifecycle(build_wallet):
    """A PENDING write followed by a SUCCESSFUL write must update one row."""
    wallet = build_wallet()
    transaction = build_transaction(wallet)
    repository = build_repository(wallet)

    repository.save(transaction)  # the PENDING record of intent
    transaction.mark_successful()
    repository.save(transaction)  # the terminal state

    stored = repository.get_by_id(transaction.transaction_id)
    assert stored.status is TransactionStatus.SUCCESSFUL
    count = repository._connection.execute(
        "SELECT COUNT(*) FROM transactions"
    ).fetchone()[0]
    assert count == 1


@pytest.mark.parametrize(
    ("transition", "expected_status"),
    [
        ("mark_successful", TransactionStatus.SUCCESSFUL),
        ("mark_failed", TransactionStatus.FAILED),
    ],
)
def test_round_trips_terminal_status(transition, expected_status, build_wallet):
    wallet = build_wallet()
    transaction = build_transaction(wallet)
    getattr(transaction, transition)()
    repository = build_repository(wallet)

    repository.save(transaction)

    stored = repository.get_by_id(transaction.transaction_id)
    assert stored.status is expected_status
    assert stored.completed_at == transaction.completed_at


def test_round_trips_a_reversed_transaction(build_wallet):
    wallet = build_wallet()
    transaction = build_transaction(wallet)
    transaction.mark_successful()
    transaction.reverse()
    repository = build_repository(wallet)

    repository.save(transaction)

    stored = repository.get_by_id(transaction.transaction_id)
    assert stored.status is TransactionStatus.REVERSED
    assert stored.completed_at == transaction.completed_at
    assert stored.reversed_at == transaction.reversed_at


def test_get_by_internal_reference_finds_a_saved_transaction(build_wallet):
    wallet = build_wallet()
    transaction = build_transaction(wallet)
    transaction.mark_successful()
    repository = build_repository(wallet)
    repository.save(transaction)

    stored = repository.get_by_internal_reference(transaction.internal_reference)

    assert stored is not None
    assert stored.transaction_id == transaction.transaction_id


def test_get_by_internal_reference_returns_none_when_absent(build_wallet):
    wallet = build_wallet()
    repository = build_repository(wallet)

    assert repository.get_by_internal_reference(str(uuid4())) is None


def test_get_by_wallet_id_returns_that_wallets_ledger_oldest_first(build_wallet):
    wallet = build_wallet()
    other_wallet = build_wallet()
    repository = build_repository(wallet)
    # Seed the second wallet so the foreign key resolves.
    SqliteWalletRepository(repository._connection).save(other_wallet)

    first = build_transaction(wallet, internal_reference=str(uuid4()))
    second = build_transaction(wallet, internal_reference=str(uuid4()))
    stranger = build_transaction(other_wallet, internal_reference=str(uuid4()))
    for transaction in (first, second, stranger):
        transaction.mark_successful()
        repository.save(transaction)

    ledger = repository.get_by_wallet_id(wallet.wallet_id)

    assert [t.transaction_id for t in ledger] == [
        first.transaction_id,
        second.transaction_id,
    ]


def test_get_by_wallet_id_is_empty_for_a_wallet_without_transactions(build_wallet):
    wallet = build_wallet()
    repository = build_repository(wallet)

    assert repository.get_by_wallet_id(wallet.wallet_id) == []


def test_get_by_provider_reference_finds_a_saved_transaction(build_wallet):
    wallet = build_wallet()
    transaction = build_transaction(wallet, provider_reference="prov-123")
    transaction.mark_successful()
    repository = build_repository(wallet)
    repository.save(transaction)

    stored = repository.get_by_provider_reference("prov-123")

    assert stored is not None
    assert stored.transaction_id == transaction.transaction_id


def test_get_by_provider_reference_returns_none_when_absent(build_wallet):
    wallet = build_wallet()
    repository = build_repository(wallet)

    assert repository.get_by_provider_reference("prov-missing") is None


def test_get_by_id_of_missing_transaction_raises(build_wallet):
    wallet = build_wallet()
    repository = build_repository(wallet)

    with pytest.raises(TransactionNotFoundError):
        repository.get_by_id(uuid4())


def test_round_trips_a_payout_with_its_destination(build_wallet):
    """The snapshot survives storage, including the rail-specific details."""
    wallet = build_wallet()
    transaction = build_transaction(
        wallet,
        type=TransactionType.PAYOUT,
        destination=DESTINATION,
    )
    transaction.mark_successful()
    repository = build_repository(wallet)

    repository.save(transaction)

    stored = repository.get_by_id(transaction.transaction_id)
    assert stored.destination == DESTINATION
    assert stored.destination.kind is DestinationKind.BANK_ACCOUNT
    assert stored.destination.detail("bank_code") == "058"


def test_a_transaction_without_a_destination_round_trips_as_none(build_wallet):
    wallet = build_wallet()
    transaction = build_transaction(wallet)
    transaction.mark_successful()
    repository = build_repository(wallet)

    repository.save(transaction)

    assert repository.get_by_id(transaction.transaction_id).destination is None


# --- the reconciliation read ------------------------------------------------


def test_list_awaiting_provider_returns_pending_rows_that_carry_a_reference(
    build_wallet,
):
    """**Both halves of the filter, and neither is decorative.**

    The reconciler asks a provider "what became of this reference?", so it may
    only ask about rows a provider was actually told about. The status half
    excludes a finished deposit; the reference half excludes every CLI
    withdrawal and plan-run payout, which are PENDING for ever as far as a
    provider is concerned because ``app/presentation/cli.py`` never writes a
    ``provider_reference`` at all.
    """
    wallet = build_wallet()
    repository = build_repository(wallet)

    awaiting = build_transaction(wallet, provider_reference="prov-awaiting")
    finished = build_transaction(wallet, provider_reference="prov-finished")
    finished.mark_successful()
    unsponsored = build_transaction(wallet)
    for transaction in (awaiting, finished, unsponsored):
        repository.save(transaction)

    found = repository.list_awaiting_provider()

    assert [t.transaction_id for t in found] == [awaiting.transaction_id]


def test_list_awaiting_provider_is_oldest_first(build_wallet):
    """The order is the contract, because the batch takes from the front.

    A run asks about the oldest rows first, so a backlog longer than one batch
    drains from the end that has been waiting longest rather than starving it -
    ``get_by_wallet_id``'s ordering rule, for the same reason and with the same
    tiebreak.
    """
    wallet = build_wallet()
    repository = build_repository(wallet)

    rows = [
        build_transaction(
            wallet,
            provider_reference=f"prov-{n}",
            created_at=datetime(2026, 1, 2, 11, n),
        )
        for n in range(3)
    ]
    for transaction in reversed(rows):
        repository.save(transaction)

    found = repository.list_awaiting_provider()

    assert [t.provider_reference for t in found] == [
        "prov-0",
        "prov-1",
        "prov-2",
    ]


def test_list_awaiting_provider_is_empty_when_nothing_is_in_flight(build_wallet):
    """The ordinary state of a healthy installation, and the one the CLI reports
    as ``nothing in flight`` rather than as silence."""
    wallet = build_wallet()
    repository = build_repository(wallet)

    assert repository.list_awaiting_provider() == []
