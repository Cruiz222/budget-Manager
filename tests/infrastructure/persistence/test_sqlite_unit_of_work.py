from decimal import Decimal
from uuid import uuid4

import pytest

from app.domain.money.currency import Currency
from app.domain.money.exception import WalletNotFoundError
from app.domain.money.money import Money
from app.domain.money.transaction import Transaction
from app.domain.money.transactionStatus import TransactionStatus
from app.domain.money.transactionType import TransactionType
from app.domain.money.wallet import Wallet
from app.domain.money.walletStatus import WalletStatus
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)

NGN = Currency.NGN


def build_wallet():
    return Wallet(
        wallet_id=uuid4(),
        user_id=uuid4(),
        status=WalletStatus.ACTIVE,
        _available_balance=Money(Decimal("10000"), NGN),
        _locked_balance=Money(Decimal("0"), NGN),
        currency=NGN,
    )


def build_successful_deposit(wallet, internal_reference):
    transaction = Transaction(
        wallet_id=wallet.wallet_id,
        type=TransactionType.DEPOSIT,
        amount=Money(Decimal("5000"), NGN),
        internal_reference=internal_reference,
    )
    transaction.mark_successful()
    return transaction


def test_rollback_discards_a_wallet_and_its_transaction(tmp_path):
    factory = SqliteUnitOfWorkFactory(str(tmp_path / "atomic.db"))
    wallet = build_wallet()
    internal_reference = str(uuid4())

    uow = factory.start()
    uow.wallets.save(wallet)
    uow.transactions.save(build_successful_deposit(wallet, internal_reference))
    uow.rollback()

    # A fresh unit - a new connection - must see neither write.
    fresh = factory.start()
    with pytest.raises(WalletNotFoundError):
        fresh.wallets.get_by_id(wallet.wallet_id)
    assert fresh.transactions.get_by_internal_reference(internal_reference) is None
    fresh.rollback()


def test_commit_persists_wallet_and_transaction_together(tmp_path):
    factory = SqliteUnitOfWorkFactory(str(tmp_path / "atomic.db"))
    wallet = build_wallet()
    wallet.apply_deposit(Money(Decimal("5000"), NGN))  # new balance: 15000
    internal_reference = str(uuid4())

    uow = factory.start()
    uow.wallets.save(wallet)
    uow.transactions.save(build_successful_deposit(wallet, internal_reference))
    uow.commit()

    fresh = factory.start()
    stored_wallet = fresh.wallets.get_by_id(wallet.wallet_id)
    assert stored_wallet.available_balance == Money(Decimal("15000"), NGN)
    stored_transaction = fresh.transactions.get_by_internal_reference(
        internal_reference
    )
    assert stored_transaction is not None
    assert stored_transaction.status is TransactionStatus.SUCCESSFUL
    fresh.rollback()


def test_rollback_keeps_the_prior_committed_balance(tmp_path):
    factory = SqliteUnitOfWorkFactory(str(tmp_path / "atomic.db"))
    wallet = build_wallet()

    first = factory.start()
    first.wallets.save(wallet)
    first.commit()

    # Second unit: a deposit that is then abandoned mid-operation.
    second = factory.start()
    stored = second.wallets.get_by_id(wallet.wallet_id)
    stored.apply_deposit(Money(Decimal("5000"), NGN))
    second.wallets.save(stored)
    second.rollback()

    fresh = factory.start()
    assert (
        fresh.wallets.get_by_id(wallet.wallet_id).available_balance
        == Money(Decimal("10000"), NGN)
    )
    fresh.rollback()
