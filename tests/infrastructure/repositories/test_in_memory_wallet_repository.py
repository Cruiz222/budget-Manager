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


def test_save_and_get_owned_returns_same_wallet(build_wallet):
    wallet = build_wallet()
    repository = InMemoryWalletRepository()

    repository.save(wallet)

    assert repository.get_owned(wallet.wallet_id, wallet.user_id) is wallet


def test_get_owned_of_an_unknown_id_raises(actor):
    repository = InMemoryWalletRepository()

    with pytest.raises(WalletNotFoundError):
        repository.get_owned(uuid4(), actor)


def test_get_owned_of_a_foreign_wallet_raises_not_found(build_wallet, actor, stranger):
    """The in-memory adapter upholds the same rule as the SQLite one.

    Worth its own test rather than assumed: this class is production code that
    implements the same port, so a scoping rule honoured by one adapter and not
    the other would be a rule that holds only where the tests happen to look.
    """
    wallet = build_wallet(user_id=stranger)
    repository = InMemoryWalletRepository()
    repository.save(wallet)

    with pytest.raises(WalletNotFoundError):
        repository.get_owned(wallet.wallet_id, actor)
