from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import secrets
import uuid

from .exception import (
    InvalidSessionExpiresAtError,
    InvalidSessionIDError,
    InvalidSessionIssuedAtError,
    InvalidSessionTokenHashError,
    InvalidSessionWindowError,
    InvalidUserIDError,
)

#: How long a session lasts, counted from the moment it was issued.
#:
#: **Absolute, not sliding**, and that is a decision rather than a simplification.
#: A sliding window - "extend it on every request" - means a session that is used
#: daily never ends and one that is used monthly ends immediately, which is the
#: opposite of what a person expects from "stay signed in". It also writes to the
#: database on every authenticated read, turning a pure read into a write and
#: making every request contend for SQLite's single writer. An absolute expiry
#: makes the whole thing a comparison against a stored moment, and there is one
#: write in a session's life: the one that creates it.
SESSION_LIFETIME = timedelta(days=30)

#: Entropy per token, in bytes. 32 bytes is 256 bits, which is the size at which
#: guessing stops being an attack and becomes a physics problem.
TOKEN_BYTES = 32


def new_session_token() -> str:
    """A fresh token, in the form the client will present it.

    ``secrets`` and not ``random``: the latter is a Mersenne Twister seeded from
    the clock, and observing a few of its outputs is enough to reconstruct its
    state and predict every later one. ``secrets`` reads from the OS CSPRNG,
    which is the whole of the difference that matters here.
    """
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_session_token(token: str) -> str:
    """What gets stored, for a token the client holds.

    **SHA-256 rather than argon2, and the difference from the password hash is the
    point.** argon2 is memory-hard because a password is a low-entropy human
    secret: an attacker who steals the table can guess billions of candidates
    offline, and the cost factor is what makes each guess expensive. A session
    token is 256 bits of CSPRNG output - there is no dictionary to run, so
    memory-hardness buys nothing, while it would be paid on *every authenticated
    request* rather than once per login. Using the expensive function where the
    input is already unguessable is a common and costly misreading of the advice.

    What the hash buys is decision 49's "hashed at rest", and it is worth being
    precise about how much: **the stored value is not a usable credential.** The
    server hashes whatever token arrives and looks *that* up, so presenting the
    stored hash would hash the hash and match nothing. Somebody who reads the
    session table learns which sessions exist and cannot use a single one of them
    - which is exactly the property a password hash does *not* have, since there
    the stored value is what an offline attack is run against.

    A plain ``sha256`` is right here for the same reason: there is no need for a
    salt, because the input has no distribution to attack and two identical tokens
    cannot occur. Salting would only make the lookup key unpredictable, which
    defeats the point of using it as the index.

    Exactly two callers - ``LogIn`` to store, ``ResolveActorFromSession`` to look
    up - which is ``fold_email``'s arrangement: one rule, two callers, no way for
    the two to drift into disagreeing about what a token hashes to.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass
class Session:
    """A record that somebody proved who they are, and until when.

    **This aggregate can hold a token's hash and never the token.** ``token_hash``
    is the only form that appears here, and the plaintext exists solely in the
    return value of ``issue`` - which is why there is no redacted ``__repr__`` on
    this class, unlike ``PlainPassword`` and ``PasswordCredential``. Printing a
    session prints a hash that cannot be presented to authenticate, so there is
    nothing here to hide.

    Expiry is **checked, not enforced by the store**: ``is_expired`` answers a
    question, and it takes the moment to answer it against. Reading the clock
    inside would put the one interesting case - a session expiring exactly now -
    beyond the reach of a test, which is the same rule ``Fund.is_matured(as_of)``
    follows and for the same reason.

    Unlike ``Fund``, this aggregate has no transition methods: there is nothing to
    extend and nothing to revoke in place, because decision 49 makes revocation a
    *deletion* rather than a flag. A revoked session is a row that is gone, which
    is a state that cannot be misread later - where ``revoked_at IS NOT NULL``
    leaves every query in the codebase obliged to remember to check it, and one
    that forgets is a session that never ended.
    """

    session_id: uuid.UUID
    user_id: uuid.UUID
    token_hash: str
    issued_at: datetime
    expires_at: datetime

    def __post_init__(self):
        if not isinstance(self.session_id, uuid.UUID):
            raise InvalidSessionIDError("invalid session id")

        if not isinstance(self.user_id, uuid.UUID):
            raise InvalidUserIDError("invalid user id")

        # Empty is refused for the reason an empty credential hash is: a row that
        # can never match is a session nobody can use, wearing the shape of one
        # that works. It would look like "I keep having to sign in again".
        if not isinstance(self.token_hash, str) or not self.token_hash:
            raise InvalidSessionTokenHashError("invalid token hash")

        # ``datetime`` and not ``date`` for both, the same narrow check every
        # aggregate here makes. It matters more here than usual: a session whose
        # timestamps came back as bare dates would compare against a moment by
        # promoting midnight, so every session would appear to expire at the start
        # of its final day.
        if not isinstance(self.issued_at, datetime):
            raise InvalidSessionIssuedAtError("invalid issued at")

        if not isinstance(self.expires_at, datetime):
            raise InvalidSessionExpiresAtError("invalid expires at")

        # A session that expires before it was issued is born dead, and it is a
        # bug rather than a state: nothing constructs one deliberately, so the only
        # way to get it is a negative lifetime or a mixed-up argument order. Left
        # alone it would present as a login that reports success and then does not
        # work - the hardest kind of failure to trace back to its cause. Note
        # ``<=`` rather than ``<``: a zero-length session is equally unusable.
        if self.expires_at <= self.issued_at:
            raise InvalidSessionWindowError(
                "a session must expire after the moment it was issued"
            )

    def is_expired(self, as_of: datetime) -> bool:
        """Whether this session has ended by ``as_of``.

        ``>=`` and not ``>``, which is the boundary worth pinning: a session is
        expired *at* the instant it expires, not a moment later. The alternative
        is a session that is valid for an instant its owner was never promised.
        """
        return as_of >= self.expires_at

    @classmethod
    def issue(
        cls, user_id: uuid.UUID, now: datetime, lifetime: timedelta = SESSION_LIFETIME
    ) -> tuple["Session", str]:
        """Start a session for this user, returning it **and its token**.

        A factory rather than something the caller assembles, because the token
        and the hash of it must be produced together or not at all. If a caller
        built the ``Session`` itself it would need a hash, and the only way to have
        one is to have made a token first - leaving the plaintext lying around in
        a local variable with nothing marking it as the one thing that must not be
        stored. Here the token leaves by exactly one route, in the return value.

        ``now`` is passed in rather than read, for the reason
        ``is_expired`` takes a moment: the caller is the only thing that knows what
        time it is, and a use case that wants to test an expired session should be
        able to say so.
        """
        token = new_session_token()
        return (
            cls(
                session_id=uuid.uuid4(),
                user_id=user_id,
                token_hash=hash_session_token(token),
                issued_at=now,
                expires_at=now + lifetime,
            ),
            token,
        )
