from dataclasses import dataclass
from datetime import datetime
import uuid

from .exception import (
    InvalidUserCreatedAtError,
    InvalidUserEmailError,
    InvalidUserGoogleSubjectError,
    InvalidUserIDError,
)


def fold_email(email: str) -> str:
    """The one spelling of an address: trimmed and lowercased.

    A module function rather than a private step inside ``User``, because the
    rule is needed in exactly one other place and that place has nothing to take
    it from: ``UserRepository.find_by_email`` looks an address up *before* a
    ``User`` exists - deciding whether one exists is the question it is asking -
    so it cannot obtain the fold by constructing the aggregate.

    Two implementations of "the same address written two ways is one account"
    would be two chances to disagree, and the disagreement would surface as a
    person unable to log in to an account that plainly exists. One function, two
    callers, no way to drift.
    """
    return email.strip().lower()


@dataclass
class User:
    """Who a wallet belongs to.

    **This is an identity, not a credential.** There is no password here, no
    hash, no salt, no token - and the absence is the design rather than a stage
    of work. How a person proves they are this user is a question about
    transport and storage, and it changes with the transport: a password today,
    a Google subject id tomorrow, a passkey the year after. The identity that
    survives all three is the one below, and it is the only part the domain has
    an opinion about. Credentials arrive with the adapter that can verify them.

    ``email`` is **folded to lowercase on construction**, and that is a rule
    rather than tidiness. ``Chinedu@Example.com`` and ``chinedu@example.com``
    are the same address - every mail system on earth treats them so, since the
    domain part is case-insensitive - which makes them the same account. A
    ``UNIQUE`` column only means what it looks like it means if one address has
    one spelling by the time it reaches the database, and the place to guarantee
    that is where the value is constructed, not in whichever adapter remembers.

    The consequence worth stating: **``User`` is not frozen.** Folding in
    ``__post_init__`` requires assignment, so this is a mutable dataclass like
    ``Wallet`` and ``SavingsPlan`` rather than a frozen one. Nothing else mutates
    it, and a future ``change_email`` belongs as a method with its checks
    alongside, not as free assignment.

    What is deliberately *not* here: an ``owns(wallet)`` method. Ownership is
    answered by the store - a scoped read either returns the wallet or reports it
    absent - and a Python check beside that would be a second place for the same
    rule to live, free to disagree with the query that actually decides.
    """

    user_id: uuid.UUID
    email: str
    google_subject: str | None
    created_at: datetime

    def __post_init__(self):
        if not isinstance(self.user_id, uuid.UUID):
            raise InvalidUserIDError("invalid user id")

        if not isinstance(self.email, str):
            raise InvalidUserEmailError("invalid user email")

        # Folded before the emptiness test rather than after, so a
        # whitespace-only address is refused as empty instead of being stored as
        # whitespace - which would be a user nobody could ever log in as.
        self.email = fold_email(self.email)

        if not self.email:
            raise InvalidUserEmailError("email must not be empty")

        if "@" not in self.email:
            # The whole of the validation, and deliberately so. A complete
            # address grammar belongs at the boundary where an address is
            # *verified* - a confirmation mail either arrives or it does not, and
            # no regex changes that. What this refuses is the value that is
            # obviously not an address at all, because catching it here keeps a
            # typo out of the table rather than out of the login form.
            raise InvalidUserEmailError("email must contain '@'")

        if self.google_subject is not None:
            if not isinstance(self.google_subject, str):
                raise InvalidUserGoogleSubjectError("invalid google subject")

            # An empty subject is refused for the reason an empty event key is:
            # the column is UNIQUE, so every account that arrived this way with
            # no subject would collide on the same empty string and the second
            # signup would fail against the first. ``None`` is the honest value
            # for "this account has no Google identity", and SQLite's UNIQUE
            # permits any number of NULLs - so the two cases stay distinct.
            #
            # Not stripped, unlike the email. A subject is an opaque identifier
            # Google issued rather than a handle a human types, so trimming it
            # would be inventing a normalisation nobody asked for.
            if not self.google_subject.strip():
                raise InvalidUserGoogleSubjectError(
                    "google subject must not be empty"
                )

        # Checked against ``datetime`` and not ``date``, which is the narrow
        # check and the one that has to be kept. ``datetime`` *is* a ``date``,
        # so the wider type would accept a bare date as a moment - and an account
        # stamped with a day rather than a moment has lost the time it was
        # created. Four other aggregates carry a test asserting the trap is still
        # true; this is the fifth.
        if not isinstance(self.created_at, datetime):
            raise InvalidUserCreatedAtError("invalid created at")
