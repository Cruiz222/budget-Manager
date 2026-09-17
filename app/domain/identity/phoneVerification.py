from dataclasses import dataclass
from datetime import datetime, timedelta
import uuid

from .exception import (
    InvalidPhoneVerificationExpiresAtError,
    InvalidPhoneVerificationIDError,
    InvalidPhoneVerificationPhoneError,
    InvalidPhoneVerificationRequestedAtError,
    InvalidPhoneVerificationSettledAtError,
    InvalidPhoneVerificationStatusError,
    InvalidPhoneVerificationTokenHashError,
    InvalidPhoneVerificationWindowError,
)
from .phoneNumber import checked_phone
from .phoneVerificationStatus import PhoneVerificationStatus
from .session import hash_session_token, new_session_token

#: How long a requested number verification stays answerable, from the moment it was
#: made.
#:
#: Ten minutes, and this is the one of the three lifetimes that is **deliberately
#: shorter than its siblings' fifteen** rather than a copy of them. ``EmailChange``
#: and ``PasswordReset`` argue for fifteen as "long enough to walk to another
#: device"; that argument does not transfer, because the device a text arrives on is
#: the device that must be in the person's hand to read it. Nothing has to be walked
#: to - the message lands where the person already is - so a code that has not been
#: typed in ten minutes has not been typed because nobody is there, and holding the
#: window open longer only prolongs the ``UNIQUE`` slot described on
#: ``PhoneVerificationStatus.EXPIRED``.
#:
#: It is its own constant rather than a reuse of either sibling, for the reason both
#: of them give about each other: three numbers agreeing would be a coincidence of
#: three arguments, and importing one would make a future change to a *mail* window
#: silently move this one with nothing to report it.
PHONE_VERIFICATION_LIFETIME = timedelta(minutes=10)


@dataclass
class PhoneVerification:
    """A request to prove a number is reachable, recorded and waiting to be answered.

    **What this is, in one line:** somebody who is not yet an account holder typed a
    number and asked to be shown to hold it, and nothing has changed yet - a code
    has to come back from the handset first. So a row here is a *pending*
    verification, and no account exists for the number.

    **This is the aggregate whose row has no account on it, and that is the whole of
    what makes it different from its two siblings.** ``EmailChange`` and
    ``PasswordReset`` both carry a ``user_id``: they are requests made *by* an
    account, about an account, and the account is the fact that ties them to
    everything else. Here the identifier *is* the subject - ``phone`` is the key -
    because the entity that would own the row does not exist until this request is
    answered. That is not a smaller version of the other two; it is the reason the
    table is keyed on the number and the other two are keyed on an account.

    **It is not a second ``PasswordReset``, and the near-miss is worth stating.**
    The two share their lifecycle - single-use, windowed, a token hashed at rest,
    three refusals distinguished by one atomic claim - and they share no fields, no
    table and no enum. Decision 168's argument applies to this pair exactly as it did
    to ``EmailChange`` and ``Confirmation``: the shape is written out again rather
    than inherited, so each copy is free to move on its own, and there is
    deliberately no shared base class for the same reason ``instruction.py`` gives
    about coupling two aggregates to deduplicate a few lines.

    **There is no method that spends this**, and that is the design rather than an
    omission - the argument ``PasswordReset`` makes, unchanged. The check-and-write
    that spends a request has to be one statement or two confirms arriving together
    would both see ``AWAITING`` and both try to create the account, and the second
    would find the ``UNIQUE`` slot on ``phone`` already taken after having done
    everything else. So the transition lives in the store's claim, which sets
    ``status`` and ``settled_at`` together, and the aggregate's job is to *read* rows
    and to refuse a row whose two halves disagree. A ``settle`` method here would be
    a second route to spending a request, and it would be a route that skips the
    window test.

    It is mutable in the same narrow sense ``EmailChange`` is: the dataclass is not
    frozen, because the fold below runs on construction, but ``__post_init__`` still
    validates every field, so a row that comes back from a store with a field of the
    wrong type fails loudly at load rather than travelling further wearing a valid
    shape.

    Expiry is **checked, not enforced by the store**: ``is_expired`` answers a
    question and takes the moment to answer it against, so the one interesting case -
    a request expiring exactly now - is reachable by a test rather than only by
    waiting.
    """

    phone_verification_id: uuid.UUID
    phone: str
    token_hash: str
    status: PhoneVerificationStatus
    requested_at: datetime
    expires_at: datetime
    settled_at: datetime | None = None

    def __post_init__(self):
        if not isinstance(self.phone_verification_id, uuid.UUID):
            raise InvalidPhoneVerificationIDError("invalid phone verification id")

        # The type check comes first so that a non-string fails as a fact about
        # *this* row rather than as ``InvalidUserPhoneError``, the reason
        # ``EmailChange`` gives about ``new_email``: ``checked_phone`` would refuse
        # this same value and would name the ``User`` aggregate, sending a reader to
        # the wrong file. What is left of the shape rule is the aggregate's own,
        # through the *same function* ``User`` calls - so a verification can never be
        # recorded for a number no account could hold.
        if not isinstance(self.phone, str):
            raise InvalidPhoneVerificationPhoneError("invalid phone")

        self.phone = checked_phone(self.phone)

        # Empty is refused for the reason an empty session token hash is: a row that
        # can never be matched is a request nobody can answer, wearing the shape of
        # one that works. It would present as a text whose code does nothing, which
        # is the hardest kind of failure to trace back.
        if not isinstance(self.token_hash, str) or not self.token_hash:
            raise InvalidPhoneVerificationTokenHashError("invalid token hash")

        if not isinstance(self.status, PhoneVerificationStatus):
            raise InvalidPhoneVerificationStatusError(
                f"status must be a PhoneVerificationStatus, "
                f"got {type(self.status).__name__}"
            )

        # ``datetime`` and not ``date``, the same narrow check every aggregate here
        # makes and for the same reason: bare dates are promoted to midnight, so
        # every request made in the afternoon would appear to expire at breakfast.
        if not isinstance(self.requested_at, datetime):
            raise InvalidPhoneVerificationRequestedAtError("invalid requested at")

        if not isinstance(self.expires_at, datetime):
            raise InvalidPhoneVerificationExpiresAtError("invalid expires at")

        # A request that expires before it was made is born dead, and it is a bug
        # rather than a state: nothing constructs one deliberately. Left alone it
        # would present as a text that arrives already dead. ``<=`` rather than
        # ``<``, because a zero-length window is equally unusable.
        if self.expires_at <= self.requested_at:
            raise InvalidPhoneVerificationWindowError(
                "a phone verification must expire after the moment it was requested"
            )

        if self.settled_at is not None and not isinstance(self.settled_at, datetime):
            raise InvalidPhoneVerificationSettledAtError(
                f"settled_at must be a datetime or None, "
                f"got {type(self.settled_at).__name__}"
            )

        # The same shape of rule ``OutboundMessage``, ``EmailChange`` and
        # ``PasswordReset`` make: the state and its explanation must agree. A spent
        # request with no moment cannot say when it was answered; an awaiting one
        # carrying a settled moment claims to be finished and unfinished at once.
        # Neither has a correct reading, and both would be written by the claim's
        # single UPDATE - so a row that disagrees is a bug in that statement rather
        # than a state to interpret.
        if self.is_settled and self.settled_at is None:
            raise InvalidPhoneVerificationSettledAtError(
                f"a {self.status.value} request must record when it settled"
            )

        if not self.is_settled and self.settled_at is not None:
            raise InvalidPhoneVerificationSettledAtError(
                f"a {self.status.value} request must not carry a settled moment"
            )

    @property
    def is_settled(self) -> bool:
        """Whether this request has been answered; only ``CONFIRMED`` is.

        ``EXPIRED`` is deliberately not asked about, and it is not an oversight: it is
        derived and never stored, so a row here is either waiting or spent. Asking
        ``status_as_of`` instead would make this property depend on a moment it was
        not given.
        """
        return self.status is PhoneVerificationStatus.CONFIRMED

    def is_expired(self, as_of: datetime) -> bool:
        """Whether this request's window has closed by ``as_of``.

        ``>=`` and not ``>``, matching ``Session.is_expired``,
        ``Confirmation.is_expired``, ``EmailChange.is_expired`` and
        ``PasswordReset.is_expired``: a request is expired *at* the instant it
        expires, not a moment later. The store's claim tests the opposite side of this
        boundary with ``expires_at > ?``, which is the pair that has to agree - and
        the direction of a disagreement would be a number claimed one instant after
        the person was promised the code had stopped working.
        """
        return as_of >= self.expires_at

    def status_as_of(self, as_of: datetime) -> PhoneVerificationStatus:
        """What to report: an ``AWAITING`` request past its window reads EXPIRED.

        **Derived rather than stored**, exactly as the three siblings derive it and
        for their reason: the only thing that would notice an expired request is a
        reader, and a reader that wrote would make a ``GET`` a write. See
        ``PhoneVerificationStatus.EXPIRED`` for what the disagreement would cost here
        specifically - a number claimed on the strength of a code that should have
        stopped working.

        A ``CONFIRMED`` request stays ``CONFIRMED`` for ever, however long ago it was
        answered: expiry is about whether a request may still be *answered*, and one
        that was answered is not un-answered by the clock.
        """
        if self.status is PhoneVerificationStatus.AWAITING and self.is_expired(as_of):
            return PhoneVerificationStatus.EXPIRED
        return self.status

    @classmethod
    def issue(
        cls,
        *,
        phone: str,
        now: datetime,
        lifetime: timedelta = PHONE_VERIFICATION_LIFETIME,
    ) -> tuple["PhoneVerification", str]:
        """Record a verification for this number, returning it **and its token**.

        A factory rather than something the caller assembles, for the reason
        ``Session.issue``, ``EmailChange.issue`` and ``PasswordReset.issue`` are: the
        id, the token and the hash of it, the requested-at moment and the expires-at
        moment must be produced together or not at all. A caller building this by hand
        would need a hash, and the only way to have one is to have made a token first
        - leaving the plaintext loose in a local variable with nothing marking it as
        the one thing that must not be stored. Here the token leaves by exactly one
        route, in the return value.

        **The token is ``Session``'s, produced by the same two functions**, and that
        is a decision rather than a coincidence to be tidied later. 256 bits of CSPRNG
        output hashed with SHA-256 is the whole of the discipline, and
        ``hash_session_token``'s docstring already counts this shape's callers and
        gives the reason: what is shared is "a 256-bit token, hashed at rest", and
        where it was first needed is not what it is about.

        **The token is not a code a person can type**, and this is the one place where
        that is worth stating rather than assuming. A 256-bit token is sixty-four
        hexadecimal characters; it is copyable and it is not memorable, and the
        difference matters the moment somebody writes the message body. Two readings
        are available and neither is chosen here, because both are about the *message*
        rather than about this row - a text carrying a link that hands the token back,
        or a short numeric code traded for the token at the gate. What this class
        guarantees either way is the property that does not depend on which: the value
        in the table is a hash, the plaintext leaves by the return value, and nothing
        downstream can recover it. See ``RequestPhoneVerification`` for where that
        question is answered.

        Why 256 bits rather than six digits is decision 167's argument, and the
        tension with what a person can type is real rather than resolved by it: a
        short code is brute-forceable and there is still no rate limiter, so the
        length is kept and the delivery problem is left to the layer that composes the
        message, where it can be solved without weakening this.

        ``now`` is passed in rather than read, so a test can age a request without a
        clock patch and so one request cannot have two ideas of when it was made.
        """
        token = new_session_token()
        return (
            cls(
                phone_verification_id=uuid.uuid4(),
                phone=phone,
                token_hash=hash_session_token(token),
                status=PhoneVerificationStatus.AWAITING,
                requested_at=now,
                expires_at=now + lifetime,
            ),
            token,
        )

    def __str__(self) -> str:
        # Never the token, and never its hash - ``Session`` has no redacted repr
        # because a hash cannot be presented, and the same holds here. What this
        # prints is where the request has got to, which is what a log line about one is
        # for. Note what it deliberately cannot print: the number is *not* here, and
        # the difference from ``EmailChange`` is that the address it prints is the
        # payload rather than the subject - this row's subject is a phone number, and
        # a phone number is the smallest enumerable identifier in this product. A log
        # line is exactly the kind of place one leaks out of.
        return f"phone verification ({self.status.value})"
