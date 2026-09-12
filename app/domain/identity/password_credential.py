from dataclasses import dataclass
from datetime import datetime
import uuid

from .exception import (
    InvalidCredentialHashError,
    InvalidCredentialUpdatedAtError,
    InvalidUserIDError,
)


@dataclass(repr=False)
class PasswordCredential:
    """How one account proves it is that account, by password.

    **This is the credential that ``User`` deliberately does not hold.** The two
    are separate aggregates because they answer different questions and have
    different lifetimes: a ``User`` is *who a wallet belongs to* and outlives every
    way of proving it, while a credential is one answer to *how do you prove it*
    and is replaced wholesale when the password changes. Decision 53 - and the
    docstring on ``User`` - states that the identity carries no credential; this
    is where the credential went instead of onto that aggregate.

    The reason it matters beyond tidiness: a ``User`` is loaded by every
    authenticated request, handed to services, and rendered by
    ``translate.user_out``. If the hash travelled on it, every one of those places
    would be holding a value that must never be printed, and the only thing
    keeping it out of a response would be somebody remembering to omit it - which
    is exactly what ``user_out`` already has to do for ``google_subject``. Here
    the hash is loaded by one use case, for the length of one login, and a
    ``User`` cannot leak what it does not have.

    ``password_hash`` is the **encoded** hash - salt, parameters and digest in one
    string, as argon2 emits it - not a bare digest. Storing the parameters with
    the hash is what makes it possible to raise the cost factor later without
    invalidating every existing password: an old hash says how it was made, so it
    can still be verified and then re-hashed on the next successful login.

    ``updated_at`` is when this password was set. It is the only moment recorded,
    because it is the only one that can change without the row being replaced -
    and it is what a future "your password is four years old" notice would read.
    """

    user_id: uuid.UUID
    password_hash: str
    updated_at: datetime

    def __post_init__(self):
        if not isinstance(self.user_id, uuid.UUID):
            raise InvalidUserIDError("invalid user id")

        if not isinstance(self.password_hash, str):
            raise InvalidCredentialHashError("invalid credential hash")

        # Empty is refused for the reason an empty Google subject is on ``User``:
        # a row holding "" is a credential that can never verify anything, so it
        # is an account nobody can log into wearing the shape of one that works.
        # The failure would surface as "my password stopped working" with nothing
        # in the database looking wrong.
        if not self.password_hash:
            raise InvalidCredentialHashError("credential hash must not be empty")

        # ``datetime`` and not ``date``, the same narrow check every other
        # aggregate here makes - see ``User``, which explains the trap.
        if not isinstance(self.updated_at, datetime):
            raise InvalidCredentialUpdatedAtError("invalid updated at")

    def __repr__(self) -> str:
        """The user and the moment, never the hash.

        A hash is not the password, but it is the thing an offline attack is run
        against, and a hash in a log file is a hash that has left the database it
        was protected in. The store is the place to look at one.
        """
        return (
            f"PasswordCredential(user_id={self.user_id!r}, "
            f"password_hash=<redacted>, updated_at={self.updated_at!r})"
        )
