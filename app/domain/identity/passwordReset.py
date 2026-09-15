from dataclasses import dataclass
from datetime import datetime, timedelta
import uuid

from .exception import (
    InvalidPasswordResetExpiresAtError,
    InvalidPasswordResetIDError,
    InvalidPasswordResetRequestedAtError,
    InvalidPasswordResetSettledAtError,
    InvalidPasswordResetStatusError,
    InvalidPasswordResetTokenHashError,
    InvalidPasswordResetUserIDError,
    InvalidPasswordResetWindowError,
)
from .passwordResetStatus import PasswordResetStatus
from .session import hash_session_token, new_session_token

#: How long a requested password reset stays answerable, from the moment it was
#: made.
#:
#: Fifteen minutes, and **deliberately a constant of its own rather than a reuse of
#: ``EMAIL_CHANGE_LIFETIME`` or ``CONFIRMATION_LIFETIME``**, both of which happen
#: to hold the same number. All three agree because all three answers are "long
#: enough to walk to another device, short enough that a forgotten request is not
#: still live tomorrow", and that is a coincidence of three arguments rather than
#: one decision. Importing either other constant would make a future change to the
#: money window silently move this one, and nothing would report it.
#:
#: What the window is *for* here is the gravest of the three. A stale confirmation
#: is a standing permission to move money and a stale change request is a live
#: credential in somebody's mailbox; a stale reset is a live credential that
#: *replaces the account's password outright*, which is the one thing a holder of
#: the mailbox is otherwise still kept out of by not knowing the old one.
PASSWORD_RESET_LIFETIME = timedelta(minutes=15)


@dataclass
class PasswordReset:
    """A request to replace an account's password, recorded and waiting to be proven.

    **What this is, in one line:** somebody who cannot log in asked to be allowed
    to set a new password, and nothing has changed yet - control of the account's
    address has to be shown first, by a token mailed to it. So a row here is a
    *pending* reset, and the account still has the password it had.

    **The row carries no payload, and that is the whole difference from
    ``EmailChange``.** That aggregate holds ``new_email`` because the address being
    moved to is the fact the request authorises and the fact the mail must name.
    Here the fact being authorised is a *new password*, and there is deliberately
    no column it could go in: a password is written down exactly once in this
    system, as an argon2 hash in ``password_credentials``, and a second copy of it
    anywhere - even hashed, even briefly - would be a second thing to protect for
    no gain. The password arrives with the confirm and lives in a local variable
    for the length of one call. What this row records is the *permission*, not the
    value.

    **It is not a second ``EmailChange`` either**, and the near-miss is worth
    stating. The two share their lifecycle - single-use, windowed, a token hashed
    at rest, three refusals distinguished by one atomic claim - and they share no
    fields, no table and no enum. Decision 168's argument applies to this pair
    exactly as it did to ``EmailChange`` and ``Confirmation``: the shape is written
    out again rather than inherited, so each copy is free to move on its own, and
    there is deliberately no shared base class for the same reason
    ``instruction.py`` gives about coupling two aggregates to deduplicate a few
    lines.

    **There is no method that spends this**, and that is the design rather than an
    omission. The check-and-write that spends a request has to be one statement or
    two concurrent confirms would both see ``AWAITING`` and both replace the
    password - the second silently overwriting the first. So the transition lives
    in the store's claim, which sets ``status`` and ``settled_at`` together, and
    the aggregate's job is to *read* rows and to refuse a row whose two halves
    disagree. A ``settle`` method here would be a second route to spending a
    request, and it would be a route that skips the window test.

    It is mutable in the same narrow sense ``EmailChange`` is: the dataclass is not
    frozen, because nothing here folds a value on construction the way ``User``
    folds an address, but ``__post_init__`` still validates every field, so a row
    that comes back from a store with a field of the wrong type fails loudly at
    load rather than travelling further wearing a valid shape.

    Expiry is **checked, not enforced by the store**: ``is_expired`` answers a
    question and takes the moment to answer it against, so the one interesting case
    - a request expiring exactly now - is reachable by a test rather than only by
    waiting.
    """

    password_reset_id: uuid.UUID
    user_id: uuid.UUID
    token_hash: str
    status: PasswordResetStatus
    requested_at: datetime
    expires_at: datetime
    settled_at: datetime | None = None

    def __post_init__(self):
        if not isinstance(self.password_reset_id, uuid.UUID):
            raise InvalidPasswordResetIDError("invalid password reset id")

        if not isinstance(self.user_id, uuid.UUID):
            raise InvalidPasswordResetUserIDError("invalid user id")

        # Empty is refused for the reason an empty session token hash is: a row
        # that can never be matched is a request nobody can answer, wearing the
        # shape of one that works. It would present as a mail whose code does
        # nothing, which is the hardest kind of failure to trace back.
        if not isinstance(self.token_hash, str) or not self.token_hash:
            raise InvalidPasswordResetTokenHashError("invalid token hash")

        if not isinstance(self.status, PasswordResetStatus):
            raise InvalidPasswordResetStatusError(
                f"status must be a PasswordResetStatus, "
                f"got {type(self.status).__name__}"
            )

        # ``datetime`` and not ``date``, the same narrow check every aggregate here
        # makes and for the same reason: bare dates are promoted to midnight, so
        # every request made in the afternoon would appear to expire at breakfast.
        if not isinstance(self.requested_at, datetime):
            raise InvalidPasswordResetRequestedAtError("invalid requested at")

        if not isinstance(self.expires_at, datetime):
            raise InvalidPasswordResetExpiresAtError("invalid expires at")

        # A request that expires before it was made is born dead, and it is a bug
        # rather than a state: nothing constructs one deliberately. Left alone it
        # would present as a mail that arrives already dead. ``<=`` rather than
        # ``<``, because a zero-length window is equally unusable.
        if self.expires_at <= self.requested_at:
            raise InvalidPasswordResetWindowError(
                "a password reset must expire after the moment it was requested"
            )

        if self.settled_at is not None and not isinstance(self.settled_at, datetime):
            raise InvalidPasswordResetSettledAtError(
                f"settled_at must be a datetime or None, "
                f"got {type(self.settled_at).__name__}"
            )

        # The same shape of rule ``OutboundMessage`` and ``EmailChange`` make: the
        # state and its explanation must agree. A spent request with no moment
        # cannot say when it was answered; an awaiting one carrying a settled moment
        # claims to be finished and unfinished at once. Neither has a correct
        # reading, and both would be written by the claim's single UPDATE - so a row
        # that disagrees is a bug in that statement rather than a state to
        # interpret.
        if self.is_settled and self.settled_at is None:
            raise InvalidPasswordResetSettledAtError(
                f"a {self.status.value} request must record when it settled"
            )

        if not self.is_settled and self.settled_at is not None:
            raise InvalidPasswordResetSettledAtError(
                f"a {self.status.value} request must not carry a settled moment"
            )

    @property
    def is_settled(self) -> bool:
        """Whether this request has been answered; only ``CONFIRMED`` is.

        ``EXPIRED`` is deliberately not asked about, and it is not an oversight:
        it is derived and never stored, so a row here is either waiting or spent.
        Asking ``status_as_of`` instead would make this property depend on a moment
        it was not given.
        """
        return self.status is PasswordResetStatus.CONFIRMED

    def is_expired(self, as_of: datetime) -> bool:
        """Whether this request's window has closed by ``as_of``.

        ``>=`` and not ``>``, matching ``Session.is_expired``,
        ``Confirmation.is_expired`` and ``EmailChange.is_expired``: a request is
        expired *at* the instant it expires, not a moment later. The store's claim
        tests the opposite side of this boundary with ``expires_at > ?``, which is
        the pair that has to agree - and the direction of a disagreement would be a
        code that replaces an account's password one instant after it was promised
        to have stopped working.
        """
        return as_of >= self.expires_at

    def status_as_of(self, as_of: datetime) -> PasswordResetStatus:
        """What to report: an ``AWAITING`` request past its window reads EXPIRED.

        **Derived rather than stored**, exactly as ``Confirmation.status_as_of``
        and ``EmailChange.status_as_of`` derive it and for their reason: the only
        thing that would notice an expired request is a reader, and a reader that
        wrote would make a ``GET`` a write. See ``PasswordResetStatus.EXPIRED`` for
        what the disagreement would cost here specifically - a code that still
        replaces a password after it should not.

        A ``CONFIRMED`` request stays ``CONFIRMED`` for ever, however long ago it
        was answered: expiry is about whether a request may still be *answered*, and
        one that was answered is not un-answered by the clock.
        """
        if self.status is PasswordResetStatus.AWAITING and self.is_expired(as_of):
            return PasswordResetStatus.EXPIRED
        return self.status

    @classmethod
    def issue(
        cls,
        *,
        user_id: uuid.UUID,
        now: datetime,
        lifetime: timedelta = PASSWORD_RESET_LIFETIME,
    ) -> tuple["PasswordReset", str]:
        """Record a request for this account, returning it **and its token**.

        A factory rather than something the caller assembles, for the reason
        ``Session.issue`` is one and ``EmailChange.issue`` is another: the id, the
        token and the hash of it, the requested-at moment and the expires-at moment
        must be produced together or not at all. A caller building this by hand
        would need a hash, and the only way to have one is to have made a token
        first - leaving the plaintext loose in a local variable with nothing marking
        it as the one thing that must not be stored. Here the token leaves by
        exactly one route, in the return value.

        **The token is ``Session``'s, produced by the same two functions**, and
        that is a decision rather than a coincidence to be tidied later. 256 bits
        of CSPRNG output hashed with SHA-256 is the whole of the discipline, and
        ``hash_session_token``'s docstring already counts this shape's callers and
        gives the reason: what is shared is "a 256-bit token, hashed at rest", and
        where it was first needed is not what it is about. A second copy of the
        discipline for this token would be a second chance to get it wrong in the
        one place an error replaces an account's password rather than spending a
        session.

        Why 256 bits rather than six digits is decision 167's argument, and it
        holds here with more force rather than less: a short code is brute-forceable
        and there is still no rate limiter, and the endpoint this would protect
        hands out the ability to take an account over.

        ``now`` is passed in rather than read, so a test can age a request without a
        clock patch and so one request cannot have two ideas of when it was made.
        """
        token = new_session_token()
        return (
            cls(
                password_reset_id=uuid.uuid4(),
                user_id=user_id,
                token_hash=hash_session_token(token),
                status=PasswordResetStatus.AWAITING,
                requested_at=now,
                expires_at=now + lifetime,
            ),
            token,
        )

    def __str__(self) -> str:
        # Never the token, and never its hash - ``Session`` has no redacted repr
        # because a hash cannot be presented, and the same holds here. What this
        # prints is where the request has got to, which is what a log line about one
        # is for. Note what it cannot print: there is no address and no password on
        # this row, so this line is shorter than ``EmailChange``'s by exactly the
        # payload it does not have.
        return f"password reset ({self.status.value})"
