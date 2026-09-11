class MoneyError(Exception):
    pass
class UnsupportedCurrencyError(MoneyError):
    pass
class CurrencyMismatchError(MoneyError):
    pass
class InvalidMoneyOperationError(MoneyError):
    pass
class MoneyDivisionByZeroError(MoneyError):
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
class WalletNotFoundError(MoneyError):
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
class InvalidWalletIDError(MoneyError):
    pass
class InvalidWalletUserIDError(MoneyError):
    pass
class TransactionAlreadySuccessfulError(MoneyError):
    pass
class TransactionAlreadyFailedError(MoneyError):
    pass
class InvalidTransactionStateError(MoneyError):
    pass
class InvalidTransactionStatusError(MoneyError):
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
class InvalidTransactionDateStamp(MoneyError):
    pass
class TransactionNotFoundError(MoneyError):
    pass
class InvalidTransactionFundIDError(MoneyError):
    pass

class InvalidDestinationKindError(MoneyError):
    pass
class InvalidDestinationIdentifierError(MoneyError):
    pass
class InvalidDestinationNameError(MoneyError):
    pass
class InvalidDestinationDetailsError(MoneyError):
    pass
class MissingDestinationDetailError(MoneyError):
    pass
class InvalidTransactionDestinationError(MoneyError):
    pass
class MissingDestinationError(MoneyError):
    pass
class UnexpectedDestinationError(MoneyError):
    pass

# --- Funds (named locked pots) -------------------------------------------
# A fund is a breakdown of a wallet's locked balance, so these are money errors
# like the rest: refusing an operation is a *domain* outcome, not a bug.
#
# Note what is deliberately not here: a "maturity passed" error for the fund
# itself. A pot that has matured is not exceptional - it is a pot that is open,
# and ``is_matured`` is a question rather than a check. The refusal happens in
# the other direction, ``FundNotMaturedError``, when something tries to take
# money out too early.
class InvalidFundIDError(MoneyError):
    pass
class InvalidFundNameError(MoneyError):
    pass
class InvalidFundKindError(MoneyError):
    pass
class InvalidFundMaturityError(MoneyError):
    pass
class InvalidFundBalanceError(MoneyError):
    pass
class InvalidFundCreatedAtError(MoneyError):
    pass
class DuplicateFundNameError(MoneyError):
    pass
class FundNotFoundError(MoneyError):
    pass
class FundNotMaturedError(MoneyError):
    pass
class MaturityNotExtendedError(MoneyError):
    pass
class InvalidWalletFundsError(MoneyError):
    pass