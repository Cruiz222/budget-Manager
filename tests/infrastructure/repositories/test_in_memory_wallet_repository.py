from decimal import Decimal
from uuid import uuid4

import pytest

from app.domain.money.currency import Currency
from app.domain.money.exception import WalletNotFoundError
from app.domain.money.money import Money
from app.domain.money.wallet import Wallet
from app.domain.money.walletStatus import WalletStatus
from app.infrastructure.repositories.in_memory_wallet_repository import (
    InMemoryWalletRepository,
)


def build_wallet():
    return Wallet(
        wallet_id=uuid4(),
        user_id=uuid4(),
        status=WalletStatus.ACTIVE,
        _available_balance=Money(Decimal("10000"), Currency.NGN),
        _locked_balance=Money(Decimal("0"), Currency.NGN),
        currency=Currency.NGN,
    )


def test_save_and_get_by_id_returns_same_wallet():
    wallet = build_wallet()
    repository = InMemoryWalletRepository()

    repository.save(wallet)

    assert repository.get_by_id(wallet.wallet_id) is wallet


def test_get_by_invalid_id_raises():
    repository = InMemoryWalletRepository()

    with pytest.raises(WalletNotFoundError):
        repository.get_by_id(uuid4())
