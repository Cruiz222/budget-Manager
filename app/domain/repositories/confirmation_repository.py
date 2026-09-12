from abc import ABC, abstractmethod
from datetime import datetime
import uuid

from app.domain.money.confirmation import Confirmation
from app.domain.money.confirmationKind import ConfirmationKind


class ConfirmationRepository(ABC):
    """Defines what a confirmation store must do (the domain does not care how).

    Four methods, and the split between them is the same split
    ``NotificationRepository`` makes: one that *claims* by attempting an insert,
    one that *progresses* an existing row, and the reads. The claim is the
    interesting one, because it is where "may this request still be answered?"
    is decided - in the store, by a single statement, rather than by a check in
    Python followed by a write.
    """

    @abstractmethod
    def add(self, confirmation: Confirmation) -> bool:
        """Insert the request, reporting whether this call is the one that did.

        A claim rather than a plain write, exactly as
        ``NotificationRepository.enqueue`` is. ``UNIQUE (wallet_id,
        internal_reference)`` means the second attempt to record the *same* key
        inserts nothing and returns ``False`` - which is how a client that retried
        a request it never saw the response to is answered with its original
        request rather than a duplicate one.

        The row count is the return value because SQLite reports zero rows
        changed when the conflict fires, so the write itself answers "was this
        mine to record?".
        """
        pass

    @abstractmethod
    def claim(
        self,
        confirmation_id: uuid.UUID,
        user_id: uuid.UUID,
        kind: ConfirmationKind,
        as_of: datetime,
    ) -> Confirmation:
        """Spend the request, atomically, and return it now ``CONFIRMED``.

        **This is the gate, and it is one statement on purpose.** The check it
        replaces - read the row, look at its status, look at its window, then
        write - has a gap between the looking and the writing, and two confirms
        arriving in that gap would both see ``AWAITING`` and both move the money.
        Here the decision and the write are the same operation, so the second one
        finds nothing to change. The same reasoning as ``plan_notices``' primary
        key, applied to a row that is *supposed* to be updated.

        ``kind`` is a parameter rather than something this method reads, so the
        claim states which operation it is authorising. A payout's confirmation
        cannot authorise a withdrawal, because the UPDATE would match no row -
        and it is reported as ``ConfirmationNotFoundError``, since the withdrawal
        request with that id does not exist whatever else does.

        The three refusals are distinguished by a follow-up read, which is safe
        because nothing about the row has changed between the two statements
        inside one transaction:

        - no such request, **or one belonging to somebody else** -
          ``ConfirmationNotFoundError``. The two are one error, for the reason
          ``WalletRepository.get_owned`` gives: telling a stranger that a request
          exists and is not theirs answers a question they have no standing to
          ask.
        - already spent - ``ConfirmationAlreadyUsedError``.
        - past its window - ``ConfirmationExpiredError``. Reachable only for a row
          still ``AWAITING``, since the two above are checked first, which is what
          makes the fallback exact rather than a guess.
        """
        pass

    @abstractmethod
    def find(
        self, wallet_id: uuid.UUID, internal_reference: str
    ) -> Confirmation | None:
        """Return the request recorded under this key on this wallet, or None.

        Not-found is an *expected* outcome here - it is what ``add`` returning
        ``False`` is answered with - so it returns None instead of raising, the
        same split ``TransactionRepository.get_by_internal_reference`` makes.

        Scoped to the wallet because the key is, by the ``UNIQUE`` above. That is
        also what makes it safe to hand a client's own key back: the key cannot
        name a request on a wallet the caller does not own, because ``wallet_id``
        is half of what identifies it.
        """
        pass

    @abstractmethod
    def get_owned(self, confirmation_id: uuid.UUID, user_id: uuid.UUID) -> Confirmation:
        """Return this actor's request, or raise ``ConfirmationNotFoundError``.

        Scoped, with no unscoped counterpart anywhere - the same shape
        ``WalletRepository.get_owned`` has and for the same reason: a read with an
        owner-shaped hole in its signature is a read somebody eventually makes. A
        request belonging to another actor is reported exactly as one that does
        not exist.
        """
        pass

    @abstractmethod
    def save(self, confirmation: Confirmation) -> Confirmation:
        """Write the request's current state.

        Only the lifecycle columns move - ``status`` and ``transaction_id``. Every
        fact about the request (what kind, how much, to where, on whose wallet,
        under which key, with what window) is written once at ``add`` and never
        rewritten, which is the row's promise that a confirmed request did what
        it said it would when it was made.
        """
        pass
