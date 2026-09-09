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


def test_save_and_get_by_id_returns_same_wallet(build_wallet):
    wallet = build_wallet()
    repository = InMemoryWalletRepository()

    repository.save(wallet)

    assert repository.get_by_id(wallet.wallet_id) is wallet


def test_get_by_invalid_id_raises():
    repository = InMemoryWalletRepository()

    with pytest.raises(WalletNotFoundError):
        repository.get_by_id(uuid4())
