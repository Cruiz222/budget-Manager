from abc import ABC, abstractmethod
from datetime import datetime

from app.domain.identity.phoneVerification import PhoneVerification


class PhoneVerificationRepository(ABC):
    """Defines what a phone-verification store must do (the domain does not care how).

    **Two methods, and the missing third is the decision.** There is no ``find``
    anywhere in this port, exactly as there is none in ``EmailChangeRepository`` or
    ``PasswordResetRepository`` - nothing in the feature ever needs to *look a request
    up*. A request is written once (``save``) and answered once
    (``claim_by_token_hash``), and both operations already end holding the row they
    acted on: the claim returns what it just spent, and the save returns what it just
    wrote. A read would have no caller, and a port method added for symmetry is a
    method the next reader has to work out the purpose of.

    Both methods are here rather than on a service because they are the only two
    statements that touch the table, and the split between them is the one that
    matters: one records an intention, and the other spends one - atomically, in a
    single statement, so two confirms arriving together cannot both succeed.

    **What this port does not do, and it is the one thing a reader will look for:** it
    never touches ``users``. Claiming a number and creating the account that holds it
    are two repositories and one unit - see the attribute's declaration on
    ``UnitOfWork`` - so the pairing is a fact about the use case's transaction rather
    than something this interface could express. A store that created the account
    itself would be a store that knew how accounts are made.
    """

    @abstractmethod
    def save(self, verification: PhoneVerification) -> PhoneVerification:
        """Write this request, superseding any request this number already has.

        **Keyed on ``phone``, and the supersede is the whole point of that key.** This
        is the same argument ``PasswordResetRepository.save`` makes about ``user_id``,
        and it is worth reading there rather than here - one pending request per
        subject, not as a rule somebody remembers to check, but because the subject is
        unique in the table and a second row for one number cannot exist. Asking again
        overwrites: the old code stops working the moment this commits, which is what
        makes "the text did not arrive, ask again" true rather than a race between two
        live codes.

        **The key is the number rather than an account, and that is the difference
        from the sibling this method is otherwise identical to.** There is no
        ``user_id`` to key on, because the account does not exist yet - and it must not,
        which is exactly what the ``UNIQUE`` slot enforces. A number with a pending
        verification is a number nobody can sign up with until the window closes,
        including its actual owner, and that is a deliberate cost rather than an
        oversight: the alternative is deciding at request time whether the number is
        free, which is both racy and an enumeration oracle.

        Unlike ``ConfirmationRepository.add``, this is an upsert and not a claim, and
        the difference follows from the key rather than from taste. A confirmation is
        keyed on the client's own ``internal_reference``, so a second write under one
        key is the same request arriving twice, and refusing to write it is right.
        Here the key is the identifier being proven, so a second write is a newer
        intention by the same person, and it should win.

        The last request per number therefore survives here after it is spent, which is
        deliberate - see ``claim_by_token_hash`` for what that buys.
        """
        pass

    @abstractmethod
    def claim_by_token_hash(
        self, token_hash: str, as_of: datetime
    ) -> PhoneVerification:
        """Spend the request this code names, atomically, returning it spent.

        **This is the gate, and it is one statement on purpose.**
        ``ConfirmationRepository.claim``'s argument, unchanged: the check it replaces -
        read the row, look at its status, look at its window, then write - has a gap
        between the looking and the writing, and two confirms arriving in that gap
        would both see ``AWAITING`` and both go on to claim the number, the second
        finding the ``UNIQUE`` slot taken only after having done everything else. Here
        the decision and the write are the same operation, so the second one finds
        nothing to change.

        **The claim is by the code alone, and the number is read *out* of the claimed
        row rather than passed in.** That is ``PasswordResetRepository``'s arrangement
        and its argument: the single fact a caller has to hold is the token, and the
        subject the request authorises - there an account, here a number - is the one
        the claimed row names. Nothing a request supplied can widen that. It is worth
        naming here specifically because the caller *does* know the number in this flow
        - it typed it a moment ago - and the temptation is to scope the ``WHERE`` by it
        as well. Declining is deliberate: scoping would mean a mistyped digit is
        refused as "that code means nothing", and the three refusals below would no
        longer fall through exactly, since a row matched by code but not by number
        would be reported as expired with an ``expires_at`` in the future.

        The token is passed as a *hash* and never as a presented value, which
        ``SessionRepository.find_by_token_hash`` establishes as the rule and ``LogOut``
        already follows: the caller hashes what arrived, and the stored value cannot
        itself be presented to authenticate. A method taking the plaintext would be one
        whose implementation could compare it directly, and the table would hold
        something usable.

        The three refusals are distinguished by a follow-up read, which is safe because
        nothing about the row has changed between the two statements inside one
        transaction:

        - no such code - ``InvalidPhoneVerificationTokenError``. One class for "never
          existed" and "the row it names is gone", for the reason
          ``InvalidSessionError`` gives: a caller who can tell those apart has an
          oracle, and the remedy is the same either way.
        - already spent - ``PhoneVerificationAlreadyUsedError``. **Reachable only
          because the row survives being spent.** Deleting it on use would collapse
          this into the case above, and "you already did this" and "that code means
          nothing" are different things to be told.
        - past its window - ``PhoneVerificationExpiredError``. Reachable only for a row
          still ``AWAITING``, since the two above are checked first, which is what makes
          the fallthrough exact rather than a guess.

        The settled moment written by this call is ``as_of``, so the row can say when
        it was answered without a second reading of the clock.
        """
        pass
