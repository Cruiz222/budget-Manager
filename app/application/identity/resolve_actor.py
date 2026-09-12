"""Turning a presented token into the person it proves. One place, one rule."""

from datetime import datetime

from app.application.unit_of_work import UnitOfWorkFactory
from app.domain.identity.exception import InvalidSessionError, UserNotFoundError
from app.domain.identity.session import hash_session_token
from app.domain.identity.user import User


class ResolveActorFromSession:
    """The user a token belongs to, or a refusal.

    **This replaces ``ResolveUserByEmail``, and the difference is the phase.**
    That class answered "who is this?" from an assertion: it was handed an
    address, found or created the account holding it, and returned it. Nothing
    was proved, which is why Phase 1b could expose no operation that moved money
    - a write would have moved a stranger's money under a name they never
    claimed. This class answers the same question from a *credential*: a token
    the caller cannot have unless a login handed it to them.

    It is still "the one place that acts before it is told who is acting", and
    after 2a that phrase has a much smaller referent. ``ResolveUserByEmail``
    acted before being told *and decided the answer*, because an unknown address
    produced a new account. This one only looks up: it is handed a token and
    reports the identity the store already associates with it, or refuses. There
    is no branch in which it creates anything, so a caller who presents a token
    it made up gets a 401 and never an account.

    **It lives here rather than in a presentation for the same reason as before,
    and the reason got sharper rather than weaker.** Two presentations resolve a
    token now - the API from an ``Authorization`` header, the CLI from its
    session file - and two copies of a token-to-identity rule are two chances to
    disagree about what counts as expired, or about which hash function turns a
    token into a lookup key. Such a disagreement has no visible symptom until
    somebody is signed in on one surface and not the other.

    **It returns a ``User`` and not a bare ``user_id``**, which is a choice worth
    stating because the id is all most callers use. The token proves who was
    issued it *at that moment*; re-reading the account on each request is what
    keeps the actor a fact about the present rather than a claim from the past,
    and it is where a later "this account was deleted or disabled" belongs. It
    also keeps ``current_actor``'s signature - and therefore every route and
    every test that depends on it - exactly as it was. The cost is one indexed
    point read per authenticated request, against a session read that already
    happened.

    **Every way a token can fail produces the same refusal.** Unknown, expired,
    or belonging to an account that no longer exists all raise
    ``InvalidSessionError``, and the class's docstring gives the reason: telling
    them apart gives a caller an oracle for which tokens were once real, and
    there is nothing a client does differently in any of the three cases, since
    all of them mean signing in again. The third case is why ``get_by_id``'s
    ``UserNotFoundError`` is caught and re-raised below rather than left to
    travel - it would otherwise reach the client as a 404, which is a different
    answer to a question with one answer.
    """

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory):
        self._unit_of_work_factory = unit_of_work_factory

    def execute(self, token: str, now: datetime) -> User:
        """The account this token authenticates, or ``InvalidSessionError``.

        ``now`` is passed in rather than read here, following
        ``Session.is_expired`` and ``Fund.is_matured``: expiry is the one thing
        about this method that is worth testing at its boundary, and a method
        that reads its own clock cannot be asked what it thinks at a particular
        moment. The presentation supplies the real moment, which makes the
        boundary where the wall clock enters the system a single, visible line.

        One unit of work for both reads, and read-only - so the ``rollback`` in
        the ``finally`` releases the connection and there is nothing to commit.
        """
        uow = self._unit_of_work_factory.start()
        try:
            session = uow.sessions.find_by_token_hash(hash_session_token(token))
            if session is None:
                raise InvalidSessionError("that token is not a valid session")

            # Checked here rather than filtered in the query, so that the rule
            # about what "expired" means has exactly one home - ``Session``, next
            # to the ``>=`` that makes a session end *at* its expiry rather than a
            # moment later. A store that filtered would put a second, silent copy
            # of that comparison in SQL.
            if session.is_expired(now):
                raise InvalidSessionError("that token is not a valid session")

            try:
                return uow.users.get_by_id(session.user_id)
            except UserNotFoundError:
                raise InvalidSessionError("that token is not a valid session") from None
        finally:
            uow.rollback()
