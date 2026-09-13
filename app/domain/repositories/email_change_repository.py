from abc import ABC, abstractmethod
from datetime import datetime

from app.domain.identity.emailChange import EmailChange


class EmailChangeRepository(ABC):
    """Defines what an email-change store must do (the domain does not care how).

    **Two methods, and the missing third is the decision.** There is no ``find``
    anywhere in this port, which looks like an omission next to
    ``ConfirmationRepository`` - and it is not one, because nothing in the feature
    ever needs to *look a request up*. A request is written once (``save``) and
    answered once (``claim_by_token_hash``), and both operations already end
    holding the row they acted on: the claim returns what it just spent, and the
    save returns what it just wrote. A read would have no caller, and a port method
    added for symmetry is a method the next reader has to work out the purpose of.

    That is also why this class is smaller than its sibling rather than shaped like
    it. The two aggregates share a lifecycle, not a surface; copying the four
    methods across because the shapes rhyme would be inventing a caller for three
    of them.

    Both methods are here rather than on a service because they are the only two
    statements that touch the table, and the split between them is the one that
    matters: one records an intention, and the other spends one - atomically, in
    a single statement, so two confirms arriving together cannot both succeed.
    """

    @abstractmethod
    def save(self, change: EmailChange) -> EmailChange:
        """Write this request, superseding any request this account already has.

        **Keyed on ``user_id``, and the supersede is the whole point of that key.**
        There is one pending change per account - not as a rule somebody remembers
        to check, but because the account's id *is* the primary key and a second
        row for one account cannot exist. Asking again overwrites: the old token
        stops working the moment this commits, which is what makes "a mistyped
        address can be corrected by asking again" true rather than a race.

        Unlike ``ConfirmationRepository.add``, this is an upsert and not a claim,
        and the difference follows from the key rather than from taste. A
        confirmation is keyed on the client's own ``internal_reference``, so a
        second write under one key is the same request arriving twice, and refusing
        to write it is right. Here the key is the *account*, so a second write is a
        newer intention by the same person, and it should win.

        The last change per account therefore survives here after it is spent, which
        is deliberate - see ``claim_by_token_hash`` for what that buys.
        """
        pass

    @abstractmethod
    def claim_by_token_hash(self, token_hash: str, as_of: datetime) -> EmailChange:
        """Spend the request this token names, atomically, returning it spent.

        **This is the gate, and it is one statement on purpose.**
        ``ConfirmationRepository.claim``'s argument, unchanged: the check it
        replaces - read the row, look at its status, look at its window, then write -
        has a gap between the looking and the writing, and two confirms arriving in
        that gap would both see ``AWAITING`` and both apply the change. Here the
        decision and the write are the same operation, so the second one finds
        nothing to change.

        The token is passed as a *hash* and never as a presented value, which
        ``SessionRepository.find_by_token_hash`` establishes as the rule and
        ``LogOut`` already follows: the caller hashes what arrived, and the stored
        value cannot itself be presented to authenticate. A method taking the
        plaintext would be one whose implementation could compare it directly, and
        the table would hold something usable.

        The three refusals are distinguished by a follow-up read, which is safe
        because nothing about the row has changed between the two statements inside
        one transaction:

        - no such token - ``InvalidEmailChangeTokenError``. One class for "never
          existed" and "the account it names is gone", for the reason
          ``InvalidSessionError`` gives: a caller who can tell those apart has an
          oracle, and the remedy is the same either way.
        - already spent - ``EmailChangeAlreadyUsedError``. **Reachable only because
          the row survives being spent.** Deleting it on use would collapse this
          into the case above, and "you already did this" and "that code means
          nothing" are different things to be told.
        - past its window - ``EmailChangeExpiredError``. Reachable only for a row
          still ``AWAITING``, since the two above are checked first, which is what
          makes the fallthrough exact rather than a guess.

        The settled moment written by this call is ``as_of``, so the row can say
        when it was answered without a second reading of the clock.
        """
        pass
