from abc import ABC, abstractmethod
from app.domain.money.transaction import Transaction

class TransactionRepository(ABC):

    @abstractmethod
    def save(self, transaction: Transaction) -> Transaction :
        pass
    @abstractmethod
    def get_by_id(self, transaction_id) -> Transaction:
        pass

    @abstractmethod
    def get_by_internal_reference(self, internal_reference: str) -> Transaction | None:
        """Return the transaction recorded under this idempotency key, or None.

        Not-found is an *expected* outcome here (it drives duplicate checks),
        so it returns None instead of raising like get_by_id.
        """
        pass

    @abstractmethod
    def get_by_wallet_id(self, wallet_id) -> list[Transaction]:
        """Return every transaction on a wallet's ledger, oldest first.

        A wallet with no transactions returns an empty list. Ordering is part
        of the contract so a caller can render the ledger as-is.
        """
        pass

    @abstractmethod
    def get_by_provider_reference(self, provider_reference: str) -> Transaction | None:
        """Return the transaction recorded under a provider key, or None.

        A provider reference is the pay-out processor's own idempotency key
        (e.g. a Paystack reference), so at most one transaction carries it.
        Not-found returns None, mirroring get_by_internal_reference.
        """
        pass

    @abstractmethod
    def list_awaiting_provider(self) -> list[Transaction]:
        """Every PENDING row that a payment provider was actually told about.

        The reconciler's discovery read, and the fourth unscoped read in this
        codebase - so it gets the same defence ``SavingsPlanRepository``'s
        ``list_by_status`` gets, which is worth restating because it is the whole
        argument: **this is discovery, not access.** It crosses owners because
        the question it answers is an installation-wide one ("which payments are
        still in flight?"), and the rows it returns each name their own wallet.
        The caller reads a reference off them and asks a third party about it;
        the wallet read that eventually follows is scoped to the owner the row
        named, through ``WalletRepository.owner_of`` and then ``get_owned``, and
        is therefore not this method's business or its doing. Nothing
        user-facing can reach it, which is ``PlanService``'s rule - the service
        fronting the plans deliberately does not expose ``list_by_status`` -
        applied to the ledger side.

        **Both halves of the filter are load-bearing.** PENDING alone would hand
        back every withdrawal and every plan-run payout typed at the CLI, none of
        which any provider has ever heard of; a provider asked about one of those
        references would answer "no such reference", and the reconciler would
        raise an alarm about a payment that was never anyone's to make. A
        provider reference alone would hand back settled rows and re-ask
        questions already answered. Only the intersection is a question worth
        asking.

        **Takes no argument, deliberately.** There is no status parameter to
        point at something else and no wallet to point at somebody else - the
        narrower the door, the fewer ways a caller can get it wrong.

        Oldest first, the contract ``get_by_wallet_id`` states and for the
        stronger version of the same reason: the reconciler works through a
        bounded batch and leaves the rest for the next run, so the order decides
        which rows wait. Oldest-first asks about the longest-waiting payments
        first, and it means a row cannot be starved by newer ones arriving behind
        it.
        """
        pass
