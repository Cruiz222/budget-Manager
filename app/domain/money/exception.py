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
class ZeroAmountDepositError(MoneyError):
    pass
class InsufficientFundsError(MoneyError):
    pass
class WalletFrozenError(MoneyError):
    pass
class WalletClosedError(MoneyError):
    pass
class ZeroAmountWithdrawalError(MoneyError):
    pass
class NegativeAmountWithdrawalError(MoneyError):
    pass