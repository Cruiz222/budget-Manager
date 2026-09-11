"""``Wallet`` construction: the states a wallet can and cannot be built in.

Two of these tests changed shape when the locked balance became derived, and it
is worth saying why rather than leaving them as a diff. ``_locked_balance=5000``
used to be a type error - a bare number where a ``Money`` belonged - and
``_locked_balance=Money(6000, USD)`` a currency mismatch. There is no
``_locked_balance`` argument any more, so both mistakes now have to be made
through a **fund**, which is where the corresponding checks moved: the collection
must be a tuple of ``Fund``s, and each fund must be in the wallet's currency.
"""

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.domain.money.currency import Currency
from app.domain.money.exception import (
    CurrencyMismatchError,
    DuplicateFundNameError,
    InvalidWalletAvailableBalanceError,
    InvalidWalletCurrencyError,
    InvalidWalletFundsError,
    InvalidWalletIDError,
    InvalidWalletStatusError,
    InvalidWalletUserIDError,
)
from app.domain.money.fund import Fund
from app.domain.money.fundKind import FundKind
from app.domain.money.money import Money
from app.domain.money.wallet import Wallet
from app.domain.money.walletStatus import WalletStatus


def a_fund(currency=Currency.NGN, name="Locked", balance="6000") -> Fund:
    """A well-formed pot, so each test below can spoil exactly one thing."""
    return Fund(
        fund_id=uuid4(),
        name=name,
        kind=FundKind.PERSONAL,
        _balance=Money(Decimal(balance), currency),
        created_at=datetime(2026, 1, 1),
    )


def test_wallet_with_invalid_id_raises_error():
    with pytest.raises(InvalidWalletIDError):
        Wallet(
            wallet_id="not-a-uuid",
            user_id=uuid4(),
            currency=Currency.NGN,
            status=WalletStatus.ACTIVE,
            _available_balance=Money(5000, Currency.NGN),
        )


def test_wallet_with_invalid_user_id_raises_error():
    with pytest.raises(InvalidWalletUserIDError):
        Wallet(
            wallet_id=uuid4(),
            user_id="not-a-uuid",
            currency=Currency.NGN,
            status=WalletStatus.ACTIVE,
            _available_balance=Money(5000, Currency.NGN),
        )


def test_wallet_with_invalid_currency_raises_error():
    with pytest.raises(InvalidWalletCurrencyError):
        Wallet(
            wallet_id=uuid4(),
            user_id=uuid4(),
            currency="OMG",
            status=WalletStatus.FROZEN,
            _available_balance=Money(10000, Currency.NGN),
        )


def test_wallet_with_invalid_status_raises_error():
    with pytest.raises(InvalidWalletStatusError):
        Wallet(
            wallet_id=uuid4(),
            user_id=uuid4(),
            currency=Currency.NGN,
            status="active",
            _available_balance=Money(5000, Currency.NGN),
        )


def test_wallet_with_invalid_available_balance_raises_error():
    with pytest.raises(InvalidWalletAvailableBalanceError):
        Wallet(
            wallet_id=uuid4(),
            user_id=uuid4(),
            currency=Currency.NGN,
            status=WalletStatus.ACTIVE,
            _available_balance=5000,
        )


def test_a_wallet_with_no_funds_is_an_empty_pot_list():
    """The default, and the state every freshly created wallet is in.

    ``locked_balance`` still answers - with zero, in the wallet's own currency -
    because it is a sum over the funds and summing none of them is a question
    with an answer.
    """
    wallet = Wallet(
        wallet_id=uuid4(),
        user_id=uuid4(),
        currency=Currency.NGN,
        status=WalletStatus.ACTIVE,
        _available_balance=Money(5000, Currency.NGN),
    )

    assert wallet.funds == ()
    assert wallet.locked_balance == Money(Decimal("0"), Currency.NGN)


def test_wallet_with_invalid_funds_raises_error():
    """``_funds`` must be a tuple, not a bare number.

    The descendant of the old ``_locked_balance=5000`` test - the same mistake,
    the same point in construction, now that the locked balance is not a field
    anyone can set.
    """
    with pytest.raises(InvalidWalletFundsError):
        Wallet(
            wallet_id=uuid4(),
            user_id=uuid4(),
            currency=Currency.NGN,
            status=WalletStatus.ACTIVE,
            _available_balance=Money(5000, Currency.NGN),
            _funds=5000,
        )


def test_funds_must_be_a_tuple_and_not_a_list():
    """A list is refused although it holds the same funds.

    Not pedantry: the tuple is what stops a caller appending a fund that has
    skipped ``open_fund``'s checks. A list would pass every other assertion in
    this file and quietly lose that property.
    """
    with pytest.raises(InvalidWalletFundsError):
        Wallet(
            wallet_id=uuid4(),
            user_id=uuid4(),
            currency=Currency.NGN,
            status=WalletStatus.ACTIVE,
            _available_balance=Money(5000, Currency.NGN),
            _funds=[a_fund()],
        )


def test_every_fund_must_be_a_fund():
    with pytest.raises(InvalidWalletFundsError):
        Wallet(
            wallet_id=uuid4(),
            user_id=uuid4(),
            currency=Currency.NGN,
            status=WalletStatus.ACTIVE,
            _available_balance=Money(5000, Currency.NGN),
            _funds=("Vacation",),
        )


def test_available_balance_currency_must_match_wallet_currency():
    with pytest.raises(CurrencyMismatchError):
        Wallet(
            wallet_id=uuid4(),
            user_id=uuid4(),
            currency=Currency.NGN,
            status=WalletStatus.ACTIVE,
            _available_balance=Money(5000, Currency.USD),
        )


def test_fund_currency_must_match_wallet_currency():
    """A pot is in its wallet's currency by construction, and this is the check
    that makes "by construction" true rather than merely intended.

    It is also why the ``funds`` table has no ``currency`` column: there is no
    second place for the currency to be written, so there is no way for the two
    to disagree.
    """
    with pytest.raises(CurrencyMismatchError):
        Wallet(
            wallet_id=uuid4(),
            user_id=uuid4(),
            currency=Currency.NGN,
            status=WalletStatus.ACTIVE,
            _available_balance=Money(5000, Currency.NGN),
            _funds=(a_fund(currency=Currency.USD),),
        )


def test_two_funds_may_not_share_a_name():
    """The name is the handle a human types, so a duplicate makes a command
    ambiguous rather than merely untidy.

    ``open_fund`` refuses this too, but that refusal only guards the path through
    it. Checking in ``__post_init__`` means the invariant holds of *every*
    wallet, including one built by a test or hydrated from rows.
    """
    with pytest.raises(DuplicateFundNameError):
        Wallet(
            wallet_id=uuid4(),
            user_id=uuid4(),
            currency=Currency.NGN,
            status=WalletStatus.ACTIVE,
            _available_balance=Money(5000, Currency.NGN),
            _funds=(a_fund(name="Vacation"), a_fund(name="Vacation")),
        )


def test_the_locked_balance_is_the_sum_of_the_funds():
    """The fact the whole design rests on, asserted directly.

    Two pots on one wallet, and the locked balance is their sum - computed on
    the way out rather than stored anywhere on the way in.
    """
    wallet = Wallet(
        wallet_id=uuid4(),
        user_id=uuid4(),
        currency=Currency.NGN,
        status=WalletStatus.ACTIVE,
        _available_balance=Money(5000, Currency.NGN),
        _funds=(
            a_fund(name="Vacation", balance="4000"),
            a_fund(name="Salary", balance="2500"),
        ),
    )

    assert wallet.locked_balance == Money(Decimal("6500"), Currency.NGN)
