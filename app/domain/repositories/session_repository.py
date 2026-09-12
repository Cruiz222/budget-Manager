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
