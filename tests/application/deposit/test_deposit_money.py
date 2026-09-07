from decimal import Decimal
from uuid import uuid4
import pytest

from app.domain.money.currency import Currency
from app.domain.money.money import Money
from app.domain.money.wallet import Wallet
from app.domain.money.walletStatus import WalletStatus
from app.domain.money.transactionStatus import TransactionStatus
from app.domain.money.transactionType import TransactionType
from app.application.deposit.deposit_money import DepositMoney
from app.domain.money.exception import (
    InvalidAmountError,
    CurrencyMismatchError,
    WalletClosedError
)


def test_successful_deposit_increases_wallet_balance_and_completes_transaction():
    wallet = Wallet(
    wallet_id=uuid4(),
    user_id=uuid4(),
    status=WalletStatus.ACTIVE,
    _available_balance=Money(Decimal("10000"), Currency.NGN),
    _locked_balance=Money(Decimal("0"), Currency.NGN),
    currency=Currency.NGN,
)

    deposit_amount = Money(Decimal("5000"), Currency.NGN)

    deposit_money = DepositMoney(wallet)

    transaction = deposit_money.execute (
    wallet_id=wallet.wallet_id,
    amount=deposit_amount
)
    
    assert wallet.available_balance == Money(Decimal("15000"), Currency.NGN)

    assert transaction.status == TransactionStatus.SUCCESSFUL



def test_deposit_with_zero_amount_fails():
    wallet = Wallet(
        wallet_id=uuid4(),
        user_id=uuid4(),
        status=WalletStatus.ACTIVE,
        _available_balance=Money(Decimal("10000"), Currency.NGN),
        _locked_balance=Money(Decimal("0"), Currency.NGN),
        currency=Currency.NGN,
    )

    deposit_amount = Money(Decimal("0"), Currency.NGN)

    deposit_money = DepositMoney(wallet)

    with pytest.raises(InvalidAmountError):
        deposit_money.execute(
            wallet_id=wallet.wallet_id,
            amount=deposit_amount,
        )

        assert wallet.available_balance == Money(Decimal("10000"), Currency.NGN)



def test_deposit_with_negative_amount_fails():
    wallet = Wallet(
        wallet_id=uuid4(),
        user_id=uuid4(),
        status=WalletStatus.ACTIVE,
        _available_balance=Money(Decimal("10000"), Currency.NGN),
        _locked_balance=Money(Decimal("0"), Currency.NGN),
        currency=Currency.NGN,
    )

    deposit_amount = Money(Decimal("-5000"), Currency.NGN)

    deposit_money = DepositMoney(wallet)

    with pytest.raises(InvalidAmountError):
        deposit_money.execute(
            wallet_id=wallet.wallet_id,
            amount=deposit_amount,
        )

    assert wallet.available_balance == Money(
        Decimal("10000"),
        Currency.NGN,
    )


def test_deposit_with_wrong_currency_fails():
    wallet = Wallet(
        wallet_id=uuid4(),
        user_id=uuid4(),
        status=WalletStatus.ACTIVE,
        _available_balance=Money(Decimal("10000"), Currency.NGN),
        _locked_balance=Money(Decimal("0"), Currency.NGN),
        currency=Currency.NGN,
    )

    deposit_amount = Money(Decimal("5000"), Currency.USD)

    deposit_money = DepositMoney(wallet)

    with pytest.raises(CurrencyMismatchError):
        deposit_money.execute(
            wallet_id=wallet.wallet_id,
            amount=deposit_amount,
        )

    assert wallet.available_balance == Money(
        Decimal("10000"),
        Currency.NGN,
    )


def test_deposit_into_closed_wallet_fails():
    wallet = Wallet(
        wallet_id=uuid4(),
        user_id=uuid4(),
        status=WalletStatus.CLOSED,
        _available_balance=Money(Decimal("10000"), Currency.NGN),
        _locked_balance=Money(Decimal("0"), Currency.NGN),
        currency=Currency.NGN,
    )

    deposit_amount = Money(Decimal("5000"), Currency.NGN)

    deposit_money = DepositMoney(wallet)

    with pytest.raises(WalletClosedError):
        deposit_money.execute(
            wallet_id=wallet.wallet_id,
            amount=deposit_amount,
        )

    assert wallet.available_balance == Money(
        Decimal("10000"),
        Currency.NGN,
    )    



def test_invalid_deposit_does_not_create_transaction():
        wallet = Wallet(
        wallet_id=uuid4(),
        user_id=uuid4(),
        status=WalletStatus.ACTIVE,
        _available_balance=Money(Decimal("10000"), Currency.NGN),
        _locked_balance=Money(Decimal("0"), Currency.NGN),
        currency=Currency.NGN,
    )
        deposit_money = DepositMoney(wallet)

        deposit_amount = Money(Decimal("0"), Currency.NGN)

        with pytest.raises(InvalidAmountError):
            deposit_money.execute(wallet_id=wallet.wallet_id, amount=deposit_amount)

        assert wallet.available_balance == Money(
        Decimal("10000"),
        Currency.NGN,
    )    