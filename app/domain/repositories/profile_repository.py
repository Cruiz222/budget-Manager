from abc import ABC, abstractmethod

from app.domain.identity.profile import Profile


class ProfileRepository(ABC):
    """Defines what a profile store must do (the domain does not care how).

    **Two methods, and the read returns ``None`` rather than raising.** That is
    the opposite of ``WalletRepository.get_owned`` and it is deliberate: a wallet
    that is missing is a broken invariant, because something in this system
    created it and holds its id. A profile that is missing is the *ordinary*
    state - every account that existed before this feature has no row at all, and
    so does every account whose holder has never filled the form in. Raising
    there would make the most common case in the system an exception, and would
    invite a caller to catch it and guess a default.

    ``find_for_user`` returns ``None`` for "nothing given yet", which ``tier_for``
    reads as ``UNVERIFIED``. The two absences - no profile, and an empty one -
    therefore arrive at the same tier by two routes and neither is an error.

    **The argument identifies the owner rather than naming a thing to be checked
    against one.** A profile is owned by exactly the person it describes, so
    there is no second id to compare and no ``(profile_id, user_id)`` pair to get
    the order of wrong. That is the same shape ``UserRepository`` has, and it
    carries the same obligation: the ``user_id`` must come from an actor that was
    already resolved, never from a request. A route that read ``user_id`` out of
    a path segment would be a route that hands out anybody's legal name.

    Note the consequence for the wallet argument: because there is no
    ``get_owned`` here, the "same error for a foreign profile and a missing one"
    rule (decision 66) is satisfied by construction rather than by a branch -
    both are ``None``, and the caller cannot tell them apart because there is
    nothing to tell apart.
    """

    @abstractmethod
    def save(self, profile: Profile) -> Profile:
        """Insert or update this profile, keyed on ``user_id``.

        One row per person, so ``user_id`` is the primary key rather than a
        foreign key beside a synthetic id - a second profile for the same user is
        not a state to be validated against, it is a state that cannot be
        written.
        """
        pass

    @abstractmethod
    def find_for_user(self, user_id) -> Profile | None:
        """Return this person's profile, or ``None`` if they have never given one."""
        pass
