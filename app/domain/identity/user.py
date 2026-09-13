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


def checked_email(email: str) -> str:
    """The address a ``User`` will hold, from the address it was handed.

    The whole of the aggregate's own email rule - a string, folded, non-empty, an
    ``@`` present - in one function, because it has two callers now and they must
    not drift: ``User.__post_init__``, which checks an address on the way in (and
    on the way *back* in, since every repository constructs a ``User`` from its
    row), and ``User.change_email``, which checks one being moved to.

    Extracted rather than written a second time in the method, and that is
    ``fold_email``'s argument one step further out: the fold itself was already
    shared, but the three rules *around* it were not, so a ``change_email`` that
    re-implemented them could enforce a different emptiness test than construction
    does. The failure would be an address this system will store but will not move
    to - or, worse, one it will move to and then refuse to load.

    Returns the folded value rather than checking in place, so a caller has no way
    to hold a checked-but-unfolded address. A function that checked and returned
    nothing would leave the fold to be remembered separately at each call site,
    which is the arrangement this exists to end.
    """
    if not isinstance(email, str):
        raise InvalidUserEmailError("invalid user email")

    # Folded before the emptiness test rather than after, so a whitespace-only
    # address is refused as empty instead of being stored as whitespace - which
    # would be a user nobody could ever log in as.
    folded = fold_email(email)

    if not folded:
        raise InvalidUserEmailError("email must not be empty")

    if "@" not in folded:
        # The whole of the validation, and deliberately so. A complete
        # address grammar belongs at the boundary where an address is
        # *verified* - a confirmation mail either arrives or it does not, and
        # no regex changes that. What this refuses is the value that is
        # obviously not an address at all, because catching it here keeps a
        # typo out of the table rather than out of the login form.
        raise InvalidUserEmailError("email must contain '@'")

    return folded


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
    it, and the one address move in the system is ``change_email`` below - a method
    with its checks alongside, rather than free assignment, which is what this
    paragraph asked for before there was a method to put them in.

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

        self.email = checked_email(self.email)

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

    def change_email(self, email: str) -> None:
        """Move this account to ``email``, after the aggregate's own check.

        **The check lives here, beside the assignment, rather than at the use case
        that calls this** - which is what this class's docstring asked for before
        there was a method to put it in. A ``User`` that anything could assign an
        address to is a ``User`` whose invariant holds only wherever somebody
        remembered to re-check it, and the write path is exactly where nobody
        remembers: ``users.save`` writes whatever the aggregate holds.

        The checked value is assigned in one expression, so a refusal cannot leave
        this account holding the address it just refused. That is not a theoretical
        ordering worry - a half-applied change is a person locked out of an address
        nobody was ever told about, since their next login would use the old one.

        What is deliberately **not** checked here is whether the address is
        *usable* (``refuse_unusable_email``'s rule). Construction has to accept
        what is already on disk - the repository builds a ``User`` from its row -
        so a rule enforced there would make every account registered before it
        existed unreadable rather than merely stranded. A change must accept exactly
        what construction does, or the two would disagree about what a ``User`` may
        hold. Usability is a policy about *minting* an address, so it belongs to the
        two operations that mint one: ``SignUp`` and the change request. See
        ``app.domain.identity.emailAddress``.
        """
        self.email = checked_email(email)
