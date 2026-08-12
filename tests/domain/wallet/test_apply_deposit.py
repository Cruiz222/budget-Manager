from uuid import uuid4

from app.domain.money.wallet import Wallet
from app.domain.money.money import Money
from app.domain.money.currency import Currency

@property
def available_balance(self):
    return self._available_balance


def test_depositing_positive_amount_increases_available_balance():
    # Arrange
    wallet = Wallet(
        wallet_id=uuid4(),
        user_id=uuid4(),
        currency=Currency.NGN,
        _available_balance=Money(5000, Currency.NGN),
        _locked_balance=Money(0, Currency.NGN),
    )

    # Act
    wallet.apply_deposit(Money(2000, Currency.NGN))

    # Assert
    assert wallet.available_balance.amount == 7000