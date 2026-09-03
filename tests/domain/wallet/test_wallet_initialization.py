from uuid import uuid4
import pytest
from app.domain.money.wallet import Wallet
from app.domain.money.money import Money
from app.domain.money.currency import Currency
from app.domain.money.walletStatus import WalletStatus
from app.domain.money.exception import (
   InvalidWalletCurrencyError
)


def test_wallet_with_invalid_currency_raises_error():
    with pytest.raises(InvalidWalletCurrencyError):
        Wallet(
            wallet_id=uuid4(),
            user_id=uuid4(),
            currency="OMG",
            status=WalletStatus.FROZEN,
            _available_balance=Money(10000, Currency.NGN),
            _locked_balance=Money(4000, Currency.NGN),
        )
       
