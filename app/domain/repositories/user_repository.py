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
    discipline that keeps it safe is not in this class: it is that **the id always
    comes from something already resolved, and never from a request.** When this
    docstring said "the auth boundary is its only caller" that was true and is no
    longer; the rule it stood for is unchanged and is restated here rather than
    widened.

    There are three callers now, and each passes an id it did not get from the
    caller: ``ResolveActorFromSession`` passes the id the presented session named,
    and ``RequestEmailChange`` and ``ConfirmEmailChange`` pass the actor they were
    handed - the second of those being the id a *claimed* change names, which is
    only reachable by presenting a token this system mailed. So the door is the
    same width; what grew is the number of things standing at it holding something
    they proved.

    Every later phase that wants a user by id should ask one of those, not this
    repository.
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

        Since an address is now optional on an account, there is a case where
        this lookup could be asked about a value that does not exist: an account
        with no email has ``NULL`` in the column, and SQL's ``=`` is never true of
        ``NULL``, so such an account is simply not found by any address - which is
        correct. What the caller must not do is hand this method ``None``, because
        the fold is applied before the comparison; the implementations guard it.
        """
        pass

    @abstractmethod
    def find_by_phone(self, phone: str) -> User | None:
        """Return the user holding this number, or None.

        ``find_by_email``'s shape and its argument, one identifier over, and the
        parallel is exact rather than approximate: the number is compared
        **folded**, by the same function ``User`` calls on construction
        (``identity.phoneNumber.fold_phone``), because the argument here is a
        candidate number that has not been through a ``User`` yet and there is no
        aggregate to take the fold from.

        The fold is load-bearing in a way the email fold is not, which is worth
        stating because it is easy to read both as tidiness. An address is only
        ever written one way by a machine; a number is written ``08012345678``,
        ``+2348012345678`` and ``2348012345678`` by *the same person on three
        occasions*, so without one canonical spelling the ``UNIQUE`` column bounds
        nothing and a login typed in the wrong form fails against an account that
        plainly exists.
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
