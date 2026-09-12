from abc import ABC, abstractmethod

from app.domain.identity.user import User


class UserRepository(ABC):
    """Defines what a user store must do (the domain does not care how).

    **This is one of two repositories in the package that are not scoped to an
    actor, and the reason is that they are where an actor comes from.** Every other
    read in the codebase receives an identity and is narrowed by it; these methods
    are what produce that identity in the first place. Asking a session who it
    belongs to cannot require already knowing the answer.

    ``SessionRepository`` is the other, and the phrasing above is corrected rather
    than new: this docstring said "the one repository" until sessions arrived, and
    the honest version of the rule is that *the boundary* is allowed to read
    without an actor - not that exactly one file is.

    Concretely, that means ``get_by_id`` here returns *any* user, and the
    discipline that keeps it safe is not in this class: it is that the auth
    boundary is its only caller, and it always passes the id it just resolved
    from the session rather than one a request supplied. Every later phase that
    wants a user by id should ask that boundary, not this repository.
    """

    @abstractmethod
    def save(self, user: User) -> User:
        """Insert or update this user, keyed on ``user_id``."""
        pass

    @abstractmethod
    def get_by_id(self, user_id) -> User:
        """Return the user with this id.

        Absence is unexpected here (a caller has a real user_id in hand), so it
        raises UserNotFoundError instead of returning None - the same split as
        WalletRepository.
        """
        pass

    @abstractmethod
    def find_by_email(self, email: str) -> User | None:
        """Return the user with this address, or None.

        **None rather than an exception, and that is the difference from
        ``get_by_id``.** "No account with this address" is an ordinary answer to
        a login attempt, not a broken invariant - and it is the answer roughly
        half the time, at a form where the caller is about to try the next thing.

        The address is compared **folded** - stripped and lowercased, by the same
        function ``User`` calls on construction (``identity.user.fold_email``).
        Sharing it rather than restating the rule is the whole point: the
        argument here is a *candidate* address that has deliberately not been
        through a ``User`` yet, so there is no aggregate to take the fold from,
        and a second copy of the rule could disagree with the first. The
        disagreement would surface as a person unable to log in to an account
        that plainly exists.
        """
        pass

    @abstractmethod
    def find_by_google_subject(self, subject: str) -> User | None:
        """Return the user holding this Google subject id, or None.

        The same shape and the same reasoning as ``find_by_email``: this is the
        lookup a Google sign-in performs before it knows who is signing in.

        Not folded or trimmed, mirroring ``User``: a subject is an opaque
        identifier Google issued, not a handle a human types, so comparing it
        exactly is comparing what was actually issued.
        """
        pass
