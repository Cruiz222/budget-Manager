from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.wallet_service import WalletService
from app.domain.money.currency import Currency
from app.domain.money.exception import WalletClosedError, WalletNotFoundError
from app.domain.money.money import Money
from app.domain.money.transactionStatus import TransactionStatus
from app.domain.money.wallet import Wallet
from app.domain.money.walletStatus import WalletStatus
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)

NGN = Currency.NGN


def build_wallet(available="10000", locked="0", status=WalletStatus.ACTIVE):
    return Wallet(
        wallet_id=uuid4(),
        user_id=uuid4(),
        status=status,
        _available_balance=Money(Decimal(available), NGN),
        _locked_balance=Money(Decimal(locked), NGN),
        currency=NGN,
    )


def build_service(tmp_path):
    factory = SqliteUnitOfWorkFactory(str(tmp_path / "wallet_service.db"))
    return WalletService(factory), factory


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


def test_deposit_loads_wallet_and_persists_new_balance(tmp_path):
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


def test_withdrawal_persists_new_balance(tmp_path):
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


def test_lock_moves_both_balances(tmp_path):
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


def test_operation_on_unknown_wallet_raises(tmp_path):
    service, factory = build_service(tmp_path)

    with pytest.raises(WalletNotFoundError):
        service.deposit(
            uuid4(),
            Money(Decimal("5000"), NGN),
            internal_reference=str(uuid4()),
        )


def test_rejected_operation_persists_a_failed_audit_row_and_no_balance_change(tmp_path):
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


def test_replaying_an_internal_reference_does_not_double_credit(tmp_path):
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
