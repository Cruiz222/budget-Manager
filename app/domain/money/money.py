from dataclasses import dataclass
from decimal import Decimal
from currency import CurrencyCode
from exception import (
    MoneyError,
    UnsupportedCurrencyError,
    CurrencyMismatchError,
    InvalidMoneyOperationError,
    ZeroDivisionError,
    UnsupportedDecimalPlaceError
)

@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: CurrencyCode

    def __post_init__(self):
        if not isinstance(self.currency, CurrencyCode):
            raise UnsupportedCurrencyError("currency not supported")
        if round(self.amount, 2) != self.amount:
            raise UnsupportedDecimalPlaceError("amount can have only two decimal places")
        
    def _valid_same_currency(self, other: "Money") -> None:
        if not isinstance(other, Money):
            raise TypeError(f"Cannot perform operation between money and {type(other).__name__}")