from dataclasses import dataclass
from datetime import datetime, timedelta
import uuid

from .emailChangeStatus import EmailChangeStatus
from .exception import (
    InvalidEmailChangeExpiresAtError,
    InvalidEmailChangeIDError,
    InvalidEmailChangeNewEmailError,
    InvalidEmailChangeRequestedAtError,
    InvalidEmailChangeSettledAtError,
    InvalidEmailChangeStatusError,
    InvalidEmailChangeTokenHashError,
    InvalidEmailChangeUserIDError,
    InvalidEmailChangeWindowError,
)
from .session import hash_session_token, new_session_token
from .user import checked_email

#: How long a requested address change stays confirmable, from the moment it was
#: made.
#:
#: Fifteen minutes, and **deliberately a constant of its own rather than a reuse of
#: ``CONFIRMATION_LIFETIME``** even though the two numbers agree today. They agree
#: because both answers are "long enough to walk to another device, short enough
#: that a forgotten request is not still live tomorrow", and that is a coincidence
#: of two arguments rather than one decision. Importing the other constant would
#: make a future change to the money window silently move this one, and nothing
#: would report it - the two are about different things (a wallet's balance, an
#: account's address) and there is no reason they must move together.
#:
#: What the window is *for* here is narrower than a confirmation's. A stale
#: confirmation is a standing permission to move money; a stale change request is a
#: live credential mailed to an address the person may no longer control - which is
#: a worse thing to leave lying in a mailbox, since an inbox is exactly where an
#: attacker with access to the *old* address is already reading.
EMAIL_CHANGE_LIFETIME = timedelta(minutes=15)


@dataclass
class EmailChange:
    """A request to move an account's address, recorded and waiting to be proven.

    **What this is, in one line:** somebody who proved the account's password asked
    to move it to a new address, and nothing has changed yet - the new address has
    to be shown to be reachable first, by a token mailed to it. So a row here is a
    *pending* change, and the account still holds the address it held.

    **It is not a ``Confirmation``, and the near-miss is worth stating.** That
    aggregate is "a request to move money out" and it requires a ``wallet_id``;
    this one moves an identity and has no wallet anywhere in it. What the two share
    is their *lifecycle*, which is why the fields below look familiar: a
    single-use request with a window, a token hashed at rest, and three refusals
    (unknown / already spent / expired) that the store distinguishes with one
    atomic claim. There is deliberately no shared base class for that - the same
    argument ``instruction.py`` makes about coupling two aggregates to deduplicate
    a few lines - so the shape is written out twice and each copy is free to move
    on its own.

    **There is no method that spends this, and that is the design rather than an
    omission.** ``Confirmation`` has no status transition either, for a reason that
    applies here unchanged: the check-and-write that spends a request has to be one
    statement or two concurrent confirms would both see ``AWAITING`` and both
    apply. So the transition lives in the store's claim, which sets ``status`` and
    ``settled_at`` together, and the aggregate's job is to *read* rows and to
    refuse a row whose two halves disagree. A ``settle`` method here would be a
    second route to spending a request, and it would be a route that skips the
    window test.

    **It is mutable, unlike it looks.** ``__post_init__`` folds the new address, so
    this is a mutable dataclass - the same consequence ``User`` records, from the
    same cause, and the reason a ``dataclass(frozen=True)`` would not work here
    either.

    Expiry is **checked, not enforced by the store**: ``is_expired`` answers a
    question and takes the moment to answer it against, so the one interesting case
    - a request expiring exactly now - is reachable by a test rather than only by
    waiting.
    """

    email_change_id: uuid.UUID
    user_id: uuid.UUID
    new_email: str
    token_hash: str
    status: EmailChangeStatus
    requested_at: datetime
    expires_at: datetime
    settled_at: datetime | None = None

    def __post_init__(self):
        if not isinstance(self.email_change_id, uuid.UUID):
            raise InvalidEmailChangeIDError("invalid email change id")

        if not isinstance(self.user_id, uuid.UUID):
            raise InvalidEmailChangeUserIDError("invalid user id")

        # The field's own type guard, before the shared rule below it, following
        # every other aggregate here: a repository that mapped a column wrongly
        # gets a class naming the *field* it got wrong. ``checked_email`` makes
        # this same test and would refuse the value anyway - but it refuses it as
        # ``InvalidUserEmailError``, which names an aggregate rather than this row
        # and would send a reader to the wrong file.
        if not isinstance(self.new_email, str):
            raise InvalidEmailChangeNewEmailError("invalid new email")

        # The aggregate's own address rule, through the *same function* ``User``
        # calls - so a request can never be recorded holding an address the account
        # could not be moved to. It is deliberately the shape rule and not the
        # usability rule: see ``User.change_email`` for why usability is a policy
        # about minting an address rather than an invariant of the value, and
        # ``app.domain.identity.emailAddress`` for the whole argument.
        self.new_email = checked_email(self.new_email)

        # Empty is refused for the reason an empty session token hash is: a row
        # that can never be matched is a request nobody can answer, wearing the
        # shape of one that works. It would present as a mail whose link does
        # nothing, which is the hardest kind of failure to trace back.
        if not isinstance(self.token_hash, str) or not self.token_hash:
            raise InvalidEmailChangeTokenHashError("invalid token hash")

        if not isinstance(self.status, EmailChangeStatus):
            raise InvalidEmailChangeStatusError(
                f"status must be an EmailChangeStatus, got {type(self.status).__name__}"
            )

        # ``datetime`` and not ``date``, the same narrow check every aggregate here
        # makes and for the same reason: bare dates are promoted to midnight, so
        # every request made in the afternoon would appear to expire at breakfast.
        if not isinstance(self.requested_at, datetime):
            raise InvalidEmailChangeRequestedAtError("invalid requested at")

        if not isinstance(self.expires_at, datetime):
            raise InvalidEmailChangeExpiresAtError("invalid expires at")

        # A request that expires before it was made is born dead, and it is a bug
        # rather than a state: nothing constructs one deliberately. Left alone it
        # would present as a mail that arrives already dead. ``<=`` rather than
        # ``<``, because a zero-length window is equally unusable.
        if self.expires_at <= self.requested_at:
            raise InvalidEmailChangeWindowError(
                "an email change must expire after the moment it was requested"
            )

        if self.settled_at is not None and not isinstance(self.settled_at, datetime):
            raise InvalidEmailChangeSettledAtError(
                f"settled_at must be a datetime or None, "
                f"got {type(self.settled_at).__name__}"
            )

        # The same shape of rule ``OutboundMessage`` makes: the state and its
        # explanation must agree. A spent request with no moment cannot say when it
        # was answered; an awaiting one carrying a settled moment claims to be
        # finished and unfinished at once. Neither has a correct reading, and both
        # would be written by the claim's single UPDATE - so a row that disagrees
        # is a bug in that statement rather than a state to interpret.
        if self.is_settled and self.settled_at is None:
            raise InvalidEmailChangeSettledAtError(
                f"a {self.status.value} request must record when it settled"
            )

        if not self.is_settled and self.settled_at is not None:
            raise InvalidEmailChangeSettledAtError(
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
        return self.status is EmailChangeStatus.CONFIRMED

    def is_expired(self, as_of: datetime) -> bool:
        """Whether this request's window has closed by ``as_of``.

        ``>=`` and not ``>``, matching ``Session.is_expired`` and
        ``Confirmation.is_expired``: a request is expired *at* the instant it
        expires, not a moment later. The alternative is a token that works for an
        instant its owner was never promised - and the store's claim tests the
        opposite side of this boundary with ``expires_at > ?``, which is the pair
        that has to agree.
        """
        return as_of >= self.expires_at

    def status_as_of(self, as_of: datetime) -> EmailChangeStatus:
        """What to report: an ``AWAITING`` request past its window reads EXPIRED.

        **Derived rather than stored**, exactly as ``Confirmation.status_as_of``
        derives it and for its reason: the only thing that would notice an expired
        request is a reader, and a reader that wrote would make a ``GET`` a write.
        See ``EmailChangeStatus.EXPIRED`` for what the disagreement would cost here
        specifically - a token that still works after it should not.

        A ``CONFIRMED`` request stays ``CONFIRMED`` for ever, however long ago it
        was answered: expiry is about whether a request may still be *answered*,
        and one that was answered is not un-answered by the clock.
        """
        if self.status is EmailChangeStatus.AWAITING and self.is_expired(as_of):
            return EmailChangeStatus.EXPIRED
        return self.status

    @classmethod
    def issue(
        cls,
        *,
        user_id: uuid.UUID,
        new_email: str,
        now: datetime,
        lifetime: timedelta = EMAIL_CHANGE_LIFETIME,
    ) -> tuple["EmailChange", str]:
        """Record a request for this account, returning it **and its token**.

        A factory rather than something the caller assembles, for the reason
        ``Session.issue`` is one and ``Confirmation.requested`` is another: the id,
        the token and the hash of it, the requested-at moment and the expires-at
        moment must be produced together or not at all. A caller building this by
        hand would need a hash, and the only way to have one is to have made a
        token first - leaving the plaintext loose in a local variable with nothing
        marking it as the one thing that must not be stored. Here the token leaves
        by exactly one route, in the return value.

        **The token is ``Session``'s, produced by the same two functions**, and
        that is a decision rather than a coincidence to be tidied later. 256 bits
        of CSPRNG output hashed with SHA-256 is the whole of the discipline: the
        stored value is not a usable credential, there is no dictionary to run, and
        the hash is always derived from a presented value rather than taken from a
        request. Restating it here would be a second copy free to drift from the
        first, and this one guards an account rather than a session.

        ``now`` is passed in rather than read, so a test can age a request without
        a clock patch and so one request cannot have two ideas of when it was made.
        """
        token = new_session_token()
        return (
            cls(
                email_change_id=uuid.uuid4(),
                user_id=user_id,
                new_email=new_email,
                token_hash=hash_session_token(token),
                status=EmailChangeStatus.AWAITING,
                requested_at=now,
                expires_at=now + lifetime,
            ),
            token,
        )

    def __str__(self) -> str:
        # Never the token, and never its hash - ``Session`` has no redacted repr
        # because a hash cannot be presented, and the same holds here. What this
        # prints is the new address and where the request has got to, which is what
        # a log line about one is for.
        return f"change to {self.new_email} ({self.status.value})"
