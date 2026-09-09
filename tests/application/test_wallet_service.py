from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.wallet_service import WalletService
from app.domain.money.currency import Currency
from app.domain.money.exception import (
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
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)

NGN = Currency.NGN


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
