from dataclasses import dataclass
from decimal import Decimal
from .currency import Currency
from .exception import (
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
    currency: Currency

    def __post_init__(self):
        # 1. Currency validation
        if not isinstance(self.currency, Currency):
            raise UnsupportedCurrencyError("currency not supported")

        # 2. Amount must be a type we can represent exactly.
        #    bool is a subclass of int, so it must be rejected explicitly.
        #    float is rejected on purpose: binary floats are not exact
        #    (0.1 * 3 == 0.30000000000000004) and money must never rely on them.
        if isinstance(self.amount, bool) or not isinstance(self.amount, (int, Decimal)):
            raise InvalidMoneyOperationError(
                "amount must be an int or Decimal, not "
                f"{type(self.amount).__name__}"
            )

        # 3. Canonicalise to Decimal so every Money stores the same type.
        #    Money(5000) and Money(Decimal("5000.00")) then compare equal.
        object.__setattr__(self, "amount", Decimal(self.amount))

        # 4. Reject non-finite values before the precision check below,
        #    because round(NaN, 2) would raise a confusing built-in error.
        if not self.amount.is_finite():
            raise InvalidMoneyOperationError("amount must be a finite number")

        # 5. Precision check (works seamlessly for negative decimals like -50.25)
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

    def __mul__(self, scalar: int | Decimal) -> "Money":
        if isinstance(scalar, bool) or not isinstance(scalar, (int, Decimal)):
            raise InvalidMoneyOperationError(
                f"Cannot multiply Money by {type(scalar).__name__}. "
                "Use an int or Decimal scalar."
            )
        # amount is Decimal, so Decimal * (int|Decimal) is exact. If the product
        # has more than two decimal places, the result's __post_init__ rejects it.
        return Money(self.amount * scalar, self.currency)

    def __rmul__(self, scalar: int | Decimal) -> "Money":
        return self.__mul__(scalar)

    def __truediv__(self, divisor: int | Decimal) -> "Money":
        if isinstance(divisor, bool) or not isinstance(divisor, (int, Decimal)):
            raise InvalidMoneyOperationError(
                f"Cannot divide Money by {type(divisor).__name__}. "
                "Use an int or Decimal divisor."
            )
        if divisor == 0:
            raise ZeroDivisionError("Cannot divide Money by zero.")

        return Money(self.amount / divisor, self.currency)

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