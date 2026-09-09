from decimal import Decimal
from uuid import uuid4

import pytest

from app.domain.money.currency import Currency
from app.domain.money.exception import WalletNotFoundError
from app.domain.money.money import Money
from app.domain.money.wallet import Wallet
from app.domain.money.walletStatus import WalletStatus
from app.infrastructure.persistence.sqlite_unit_of_work import open_sqlite_connection
from app.infrastructure.repositories.sqlite_wallet_repository import (
    SqliteWalletRepository,
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


def build_repository():
    return SqliteWalletRepository(open_sqlite_connection(":memory:"))


def test_save_and_get_by_id_round_trips_the_wallet():
    wallet = build_wallet()
    repository = build_repository()

    repository.save(wallet)

    stored = repository.get_by_id(wallet.wallet_id)
    assert stored.wallet_id == wallet.wallet_id
    assert stored.user_id == wallet.user_id
    assert stored.status is WalletStatus.ACTIVE
    assert stored.currency is NGN
    assert stored.available_balance == wallet.available_balance
    assert stored.locked_balance == wallet.locked_balance


def test_save_overwrites_an_existing_wallet():
    wallet = build_wallet()
    repository = build_repository()
    repository.save(wallet)

    wallet.apply_deposit(Money(Decimal("5000"), NGN))
    repository.save(wallet)

    stored = repository.get_by_id(wallet.wallet_id)
    assert stored.available_balance == Money(Decimal("15000"), NGN)


def test_get_by_id_of_missing_wallet_raises():
    repository = build_repository()

    with pytest.raises(WalletNotFoundError):
        repository.get_by_id(uuid4())
