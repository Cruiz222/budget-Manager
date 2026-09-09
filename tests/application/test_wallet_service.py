from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.wallet_service import WalletService
from app.domain.money.currency import Currency
from app.domain.money.exception import WalletNotFoundError
from app.domain.money.money import Money
from app.domain.money.transactionStatus import TransactionStatus
from app.domain.money.wallet import Wallet
from app.domain.money.walletStatus import WalletStatus
from app.infrastructure.repositories.in_memory_transaction_repository import (
    InMemoryTransactionRepository,
)
from app.infrastructure.repositories.in_memory_wallet_repository import (
    InMemoryWalletRepository,
)

NGN = Currency.NGN


def build_wallet(available="10000", locked="0"):
    return Wallet(
        wallet_id=uuid4(),
        user_id=uuid4(),
        status=WalletStatus.ACTIVE,
        _available_balance=Money(Decimal(available), NGN),
        _locked_balance=Money(Decimal(locked), NGN),
        currency=NGN,
    )


def build_service():
    wallet_repository = InMemoryWalletRepository()
    transaction_repository = InMemoryTransactionRepository()
    service = WalletService(wallet_repository, transaction_repository)
    return service, wallet_repository


def test_deposit_by_wallet_id_loads_wallet_and_persists_new_balance():
    wallet = build_wallet()
    service, wallet_repository = build_service()
    wallet_repository.save(wallet)

    transaction = service.deposit(
        wallet.wallet_id,
        Money(Decimal("5000"), NGN),
        internal_reference=str(uuid4()),
    )

    assert transaction.status is TransactionStatus.SUCCESSFUL
    stored = wallet_repository.get_by_id(wallet.wallet_id)
    assert stored.available_balance == Money(Decimal("15000"), NGN)


def test_withdrawal_by_wallet_id_persists_new_balance():
    wallet = build_wallet()
    service, wallet_repository = build_service()
    wallet_repository.save(wallet)

    service.withdraw(
        wallet.wallet_id,
        Money(Decimal("3000"), NGN),
        internal_reference=str(uuid4()),
    )

    stored = wallet_repository.get_by_id(wallet.wallet_id)
    assert stored.available_balance == Money(Decimal("7000"), NGN)


def test_lock_by_wallet_id_moves_both_balances():
    wallet = build_wallet()
    service, wallet_repository = build_service()
    wallet_repository.save(wallet)

    service.lock(
        wallet.wallet_id,
        Money(Decimal("3000"), NGN),
        internal_reference=str(uuid4()),
    )

    stored = wallet_repository.get_by_id(wallet.wallet_id)
    assert stored.available_balance == Money(Decimal("7000"), NGN)
    assert stored.locked_balance == Money(Decimal("3000"), NGN)


def test_operation_on_unknown_wallet_raises():
    service, _ = build_service()

    with pytest.raises(WalletNotFoundError):
        service.deposit(
            uuid4(),
            Money(Decimal("5000"), NGN),
            internal_reference=str(uuid4()),
        )
