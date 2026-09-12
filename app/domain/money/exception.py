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

# --- Closing a wallet -----------------------------------------------------
# Two refusals that ``Wallet.close`` and ``WalletService.close_wallet`` can
# produce, and they are separate names because they have separate remedies: one
# is answered by moving money out, the other by cancelling a plan. Collapsing
# them into one "cannot close" error would leave the caller to guess which.
#
# ``WalletAlreadyClosedError`` above is not one of these. It answers a second
# ``close`` on a wallet that is already shut - nothing to remedy, the command
# already happened - whereas both of these are about what is still inside.
class WalletNotEmptyError(MoneyError):
    pass
class WalletHasActivePlansError(MoneyError):
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
class InvalidFundSealedAtError(MoneyError):
    pass
class InvalidFundFirstFundedAtError(MoneyError):
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

# --- Confirmations (second-level confirmation) ----------------------------
# A confirmation is a recorded request that nothing has been done about, held
# until its requester answers it. The vocabulary collision it was named around
# is worth stating once here, because it is the reason this group exists under
# this name at all: the README already says "the pending intent" for the PENDING
# *transaction*, which is a movement that *has* happened and cannot yet be
# called finished. A confirmation is the opposite - a request that has not
# happened. Two opposite states must not share one word.
#
# The guards below split into three groups, and the split is the one every
# aggregate here uses:
#
#   1. Construction guards - a malformed record. These can only fire from a bug
#      or a hand-written row, never from a person using the system, and they are
#      graded 400 with everything else that is a MoneyError.
#   2. The pairing guards - a confirmation that names the wrong fields for its
#      kind. ``Instruction`` enforces the same shape one aggregate over
#      (a payout must carry a destination and nothing else may), and the reason
#      it is written out again rather than shared is the same: coupling two
#      aggregates to deduplicate four lines would trade a little repetition for
#      a much harder-to-move boundary.
#   3. The three that reach a caller - not found (404), expired (409) and
#      already used (409). These are things a person can actually do.
class InvalidConfirmationIDError(MoneyError):
    pass
class InvalidConfirmationUserIDError(MoneyError):
    pass
class InvalidConfirmationWalletIDError(MoneyError):
    pass
class InvalidConfirmationKindError(MoneyError):
    pass
class InvalidConfirmationStatusError(MoneyError):
    pass
class InvalidConfirmationReferenceError(MoneyError):
    pass
class InvalidConfirmationAmountError(MoneyError):
    pass
class InvalidConfirmationCreatedAtError(MoneyError):
    pass
class InvalidConfirmationExpiresAtError(MoneyError):
    pass
class InvalidConfirmationTransactionIDError(MoneyError):
    pass
class InvalidConfirmationWindowError(MoneyError):
    pass
class MissingConfirmationDestinationError(MoneyError):
    pass
class UnexpectedConfirmationDestinationError(MoneyError):
    pass
class UnexpectedConfirmationAmountError(MoneyError):
    pass
class UnexpectedConfirmationFundError(MoneyError):
    pass
class ConfirmationNotFoundError(MoneyError):
    pass
class ConfirmationExpiredError(MoneyError):
    pass
class ConfirmationAlreadyUsedError(MoneyError):
    pass