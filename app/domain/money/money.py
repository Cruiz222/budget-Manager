from dataclasses import dataclass
from decimal import Decimal
from currency import CurrencyCode
from exception import (
    MoneyError,
    UnsupportedCurrencyError,
    CurrencyMismatchError,
    InvalidMoneyOperationError,
    ZeroDivisionError,
    UnsupportedDecimalPlaceError,
)


@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: CurrencyCode

    def __post_init__(self):
        # 1. Currency validation
        if not isinstance(self.currency, CurrencyCode):
            raise UnsupportedCurrencyError("currency not supported")

        # 2. Precision check (works seamlessly for negative decimals like -50.25)
        if round(self.amount, 2) != self.amount:
            raise UnsupportedDecimalPlaceError(
                "amount can have only two decimal places"
            )

    # --- Domain Helper Properties ---

    @property
    def is_debt(self) -> bool:
        """Returns True if the amount represents a negative balance / overdraft."""
        return self.amount < 0

    def abs(self) -> "Money":
        """Returns a new Money object with the absolute (positive) amount."""
        return Money(abs(self.amount), self.currency)

    def _valid_same_currency(self, other: "Money") -> None:
        """Helper to validate type and matching currency for operations."""
        if not isinstance(other, Money):
            raise InvalidMoneyOperationError(
                f"Cannot perform operation between Money and {type(other).__name__}."
            )
        if self.currency != other.currency:
            raise CurrencyMismatchError(
                f"Cannot operate on different currencies: {self.currency.value} and {other.currency.value}."
            )

    # --- Arithmetic Operators ---

    def __add__(self, other: "Money") -> "Money":
        self._valid_same_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: "Money") -> "Money":
        self._valid_same_currency(other)
        return Money(self.amount - other.amount, self.currency)

    def __neg__(self) -> "Money":
        """Supports unary negation: -money (e.g., turns +100 into -100)."""
        return Money(-self.amount, self.currency)

    def __mul__(self, scalar: int | float | Decimal) -> "Money":
        if not isinstance(scalar, (int, float, Decimal)):
            raise InvalidMoneyOperationError(
                f"Cannot multiply Money by {type(scalar).__name__}."
            )
        return Money(self.amount * Decimal(str(scalar)), self.currency)

    def __rmul__(self, scalar: int | float | Decimal) -> "Money":
        return self.__mul__(scalar)

    def __truediv__(self, divisor: int | float | Decimal) -> "Money":
        if not isinstance(divisor, (int, float, Decimal)):
            raise InvalidMoneyOperationError(
                f"Cannot divide Money by {type(divisor).__name__}."
            )
        if divisor == 0:
            raise ZeroDivisionError("Cannot divide Money by zero.")

        return Money(self.amount / Decimal(str(divisor)), self.currency)

    # --- Comparison Operators ---

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return False
        return self.amount == other.amount and self.currency == other.currency

    def __lt__(self, other: "Money") -> bool:
        self._valid_same_currency(other)
        return self.amount < other.amount

    def __le__(self, other: "Money") -> bool:
        self._valid_same_currency(other)
        return self.amount <= other.amount

    def __gt__(self, other: "Money") -> bool:
        self._valid_same_currency(other)
        return self.amount > other.amount

    def __ge__(self, other: "Money") -> bool:
        self._valid_same_currency(other)
        return self.amount >= other.amount

    # --- Representations ---

    def __str__(self) -> str:
        return f"{self.amount:.2f} {self.currency.value}"

    def __repr__(self) -> str:
        return f"Money(amount=Decimal('{self.amount}'), currency=CurrencyCode.{self.currency.name})"