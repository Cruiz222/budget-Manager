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
class InvalidAmountError(MoneyError):
    pass
class WalletAlreadyFrozenError(MoneyError):
    pass
class WalletAlreadyClosedError(MoneyError):
    pass
class WalletAlreadyActiveError(MoneyError):
    pass
class NegativeAmountDepositError(MoneyError):
    pass
class InvalidWalletCurrencyError(MoneyError):
    pass
class InvalidWalletStatusError(MoneyError):
    pass
class InvalidWalletAvailableBalanceError(MoneyError):
    pass
class InvalidWalletLockedBalanceError(MoneyError):
    pass
class TransactionAlreadySuccessfulError(MoneyError):
    pass
class TransactionAlreadyFailedError(MoneyError):
    pass
class InvalidTransactionStateError(MoneyError):
    pass 
class TransactionAlreadyReversedError(MoneyError):
    pass
class InvalidTransactionWalletIDError(MoneyError):
    pass
class InvalidTransactionTypeError(MoneyError):
    pass
class InvalidTransactionAmountError(MoneyError):
    pass
class InvalidInternalReference(MoneyError):
    pass
class InvalidproviderReference(MoneyError):
    pass
class InvalidTransactionNarration(MoneyError):
    pass
class InvalidMetaData(MoneyError):
    pass