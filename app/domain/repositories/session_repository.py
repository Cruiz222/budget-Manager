from abc import ABC, abstractmethod

from app.domain.identity.session import Session


class SessionRepository(ABC):
    """Defines what a session store must do.

    **This is the second repository in the package that is not scoped to an
    actor**, and ``UserRepository``'s docstring used to say it was the only one.
    Both are the same shape of exception for the same reason: they are where an
    actor *comes from*. A token is looked up before the caller is known, because
    looking it up is the thing that makes them known - so a scoped read here would
    require the answer to the question it is asking.

    The discipline that keeps this safe is not in this class either. It is that
    the boundary is the only caller, and that nothing above it ever passes a
    ``token_hash`` a request supplied without hashing it first - the hash is
    derived from the presented token by ``hash_session_token``, never taken from
    the request.

    **Revocation is deletion** (decision 49), which is why there is no ``revoke``
    and no ``revoked_at``. A deleted row cannot be misread; a flag obliges every
    query in the codebase to remember to filter on it, and the one that forgets is
    a session that never ended.
    """

    @abstractmethod
    def save(self, session: Session) -> Session:
        """Insert this session, keyed on ``session_id``."""
        pass

    @abstractmethod
    def find_by_token_hash(self, token_hash: str) -> Session | None:
        """Return the session this hash belongs to, or ``None``.

        ``None`` for an unknown token, and note the session it returns may still
        be expired - this answers "is there a session for this hash", and
        ``Session.is_expired`` answers "and is it still good". Keeping those two
        questions apart is what lets expiry be tested without a store and a store
        be tested without a clock.

        **Expired sessions are not filtered here**, deliberately. Filtering would
        mean this method quietly performed a deletion, and a read that sometimes
        writes is the kind of thing that is discovered during an incident. Purging
        is a separate concern for a later phase.
        """
        pass

    @abstractmethod
    def delete_by_token_hash(self, token_hash: str) -> None:
        """Remove the session with this hash, if it is there.

        By hash rather than by ``session_id`` because the hash is what a logout
        actually has: the client presents a token, and the row it identifies is
        found by the same derivation an authenticated request uses. Requiring the
        id would mean reading the session first, in a second unit of work, to
        learn something the hash already determines.

        **Idempotent, and it must not raise when nothing matched.** The row being
        absent is the state the caller was asking for, so reporting it as failure
        would turn "sign out twice" into an error.
        """
        pass

    @abstractmethod
    def delete_by_user_id(self, user_id) -> int:
        """Remove every session this account holds, returning how many there were.

        **The one scoped method in this package whose scope is an account rather
        than an actor, and it is not the hole that makes it look like.** Every
        other scoped write takes its owner from a resolved actor, which is what
        makes a caller unable to name somebody else. Here the caller *is* the
        account being acted on more directly than any actor could express: the
        account's id is read off a row this system wrote and mailed, never off a
        request - see ``ConfirmPasswordReset``, which is given a token and nothing
        else. So the rule the scoped repositories protect - "you cannot name
        another account" - is kept, and kept by a stronger argument than a session
        can make.

        **A password reset is the only caller**, and it is the reason this exists.
        ``EmailChange`` deliberately does *not* revoke anything (decision 175),
        because a session is bound to a ``user_id`` and a change of address cannot
        orphan one - and because an attacker who holds both the mailed token and
        the password simply logs in again. Neither half of that holds for a reset.
        The premise of a reset is that somebody else may know the old password, so
        the sessions opened with it are exactly the sessions that are no longer
        trustworthy; and every device signed in as the account outlives the change
        unless something ends it. So this is not a measure that fails to measure
        much - it is the one place in the system where revocation is the point.

        The count is returned rather than ignored, and it is not decoration: the
        use case reports it to the person ("every device has been signed out"),
        which is the fact that makes a stolen session's disappearance visible
        rather than silent.

        **It mirrors ``delete_by_token_hash`` on the one thing that matters**:
        deleting nothing is not an error. An account with no sessions, or one whose
        sessions were already ended, gets ``0`` and no exception - the postcondition
        is "this account holds no sessions", and it is already true.
        """
        pass
