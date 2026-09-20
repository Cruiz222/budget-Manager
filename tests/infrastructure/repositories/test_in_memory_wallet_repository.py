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


def test_list_for_owner_gives_back_what_they_hold(build_wallet, actor):
    repository = InMemoryWalletRepository()
    wallet = build_wallet()
    repository.save(wallet)

    assert repository.list_for_owner(actor) == [wallet]


def test_list_for_owner_of_somebody_who_holds_nothing(build_wallet, actor, stranger):
    """**An empty list, where ``get_owned`` raises.** The difference is the
    question: ``get_owned`` is asked about one wallet a caller already has an id
    for, so finding nothing is a contradiction; this is asked by somebody who
    does not know what they hold, and holding nothing is ordinary."""
    repository = InMemoryWalletRepository()
    repository.save(build_wallet(user_id=stranger))

    assert repository.list_for_owner(actor) == []


def test_list_for_owner_leaves_out_everybody_elses(build_wallet, actor, stranger):
    """**The scoping, which is the entire content of the method.**

    Two wallets, two owners, one answer - and the assertion is about the
    *absence* rather than about the length, so it would still fail if the other
    wallet were returned alongside.
    """
    repository = InMemoryWalletRepository()
    mine = build_wallet()
    theirs = build_wallet(user_id=stranger)
    repository.save(theirs)
    repository.save(mine)

    assert repository.list_for_owner(actor) == [mine]


def test_list_for_owner_is_oldest_first(build_wallet, actor):
    """The order the port asks for, and the reason it asks for one at all: the
    page a person lands on draws these in order, and an order that came from a
    dict's iteration in one adapter and a plan in the other would put somebody's
    wallets in two different orders depending on the store behind them."""
    repository = InMemoryWalletRepository()
    first = build_wallet()
    second = build_wallet()
    third = build_wallet()
    for wallet in (first, second, third):
        repository.save(wallet)

    assert repository.list_for_owner(actor) == [first, second, third]


def test_list_for_owner_keeps_the_others_between_yours(build_wallet, actor, stranger):
    """**Interleaved on purpose.** A filter written as "stop at the first wallet
    that is not mine" would pass every test above, because they save in blocks.
    Here somebody else's wallet sits in the middle of the sequence, so an
    implementation that stopped early would come back short."""
    repository = InMemoryWalletRepository()
    first = build_wallet()
    second = build_wallet()
    repository.save(first)
    repository.save(build_wallet(user_id=stranger))
    repository.save(second)

    assert repository.list_for_owner(actor) == [first, second]


def test_list_for_owner_does_not_hand_back_the_stores_own_list(build_wallet, actor):
    """A caller that sorted or truncated what it was given must not be editing
    the store. The list is built per call rather than being a view onto the
    dict's values, and one mutation is enough to show which it is."""
    repository = InMemoryWalletRepository()
    repository.save(build_wallet())

    repository.list_for_owner(actor).clear()

    assert len(repository.list_for_owner(actor)) == 1

