class MoneyError(Exception):
    pass
class UnsupportedCurrencyError(MoneyError):
    pass
class CurrencyMismatchError(MoneyError):
    pass
class InvalidMoneyOperationError(MoneyError):
    pass
class ZeroDivisionError(MoneyError):
    pass
class UnsupportedDecimalPlaceError(MoneyError):
    pass