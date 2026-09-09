from app.domain.money.exception import InvalidAmountError, MoneyError
from app.domain.money.money import Money
from app.domain.money.transaction import Transaction
from app.domain.money.transactionType import TransactionType
from app.domain.money.wallet import Wallet
from app.domain.repositories.transaction_repository import TransactionRepository


class DepositMoney:
    """Deposit money into a wallet, keeping wallet and transaction in step.

    The use case depends only on the domain: Wallet and Transaction for rules,
    and the abstract TransactionRepository for persistence. The caller injects
    a concrete repository, so this layer never imports infrastructure.
    """

    def __init__(self, wallet: Wallet, transaction_repository: TransactionRepository):
        self.wallet = wallet
        self.transaction_repository = transaction_repository

    def execute(self, amount: Money, internal_reference: str) -> Transaction:
        # 1. Command validation happens before any record exists: a deposit of
        #    zero, a negative amount, or a non-Money value is not a financial
        #    event, so no transaction is created for it.
        if not isinstance(amount, Money):
            raise InvalidAmountError("deposit amount must be a Money")
        if amount.amount <= 0:
            raise InvalidAmountError("deposit amount must be greater than zero")

        # 2. Idempotency: internal_reference is the caller's key for THIS
        #    logical deposit. If a transaction with the same key already
        #    exists, the deposit was already processed - return its record
        #    instead of crediting the wallet a second time. This is what stops
        #    double-charging when a client retries or a webhook is duplicated.
        existing = self.transaction_repository.get_by_internal_reference(
            internal_reference
        )
        if existing is not None:
            return existing

        # 3. Record intent BEFORE touching money. The attempt is persisted as
        #    PENDING so it can never silently disappear.
        transaction = Transaction(
            wallet_id=self.wallet.wallet_id,
            type=TransactionType.DEPOSIT,
            amount=amount,
            internal_reference=internal_reference,
        )
        self.transaction_repository.save(transaction)

        try:
            # 4. The wallet is the authority on whether this deposit is allowed.
            #    It validates and raises before mutating its balance.
            self.wallet.apply_deposit(amount)
        except MoneyError:
            # 4a. The wallet rejected the deposit (closed wallet, wrong currency,
            #     ...). Record the attempt as FAILED so there is an audit trail,
            #     then surface the original error to the caller.
            transaction.mark_failed()
            self.transaction_repository.save(transaction)
            raise

        # 4b. The money moved successfully - the transaction is complete.
        transaction.mark_successful()
        self.transaction_repository.save(transaction)

        return transaction
