"""Ending a session: the token is both what you present and what you delete."""

from app.application.unit_of_work import UnitOfWorkFactory
from app.domain.identity.session import hash_session_token


class LogOut:
    """Stop a token from authenticating anything.

    **This is the one operation in the codebase that is authorised by the thing
    it destroys**, and that is not a loophole - it is what makes the method
    correct. Every other use case takes an actor that has already been resolved
    and asks whether that actor may touch a resource; here the caller presents a
    token and asks for *that token* to stop working. Proving you hold it and
    being entitled to end it are the same act, so there is nothing to check and
    no actor to resolve.

    That is why it takes a token rather than a ``User``. Giving it an actor would
    mean resolving the session first - which can only fail for a token that is
    expired or unknown, and those are precisely the tokens a client most needs to
    be able to discard. A logout that answers "not authenticated" to somebody
    signing out has failed at the one thing it does.

    **It is idempotent, and it does not check that the session existed.** The
    postcondition is "this token does not authenticate", and that is already true
    if the token was never valid, if it expired, or if this is the second time.
    Reporting those as errors would make every caller handle a failure that is
    indistinguishable from success in every way that matters. See
    ``SessionRepository.delete_by_token_hash``, which refuses to raise for a
    delete that matched nothing, for the same reason one layer down.
    """

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory):
        self._unit_of_work_factory = unit_of_work_factory

    def execute(self, token: str) -> None:
        """Delete the session this token belongs to, if there is one.

        The token is hashed before it is used as a key, and that is the
        discipline ``SessionRepository`` names as the thing keeping its unscoped
        read safe: the hash is always *derived* from a presented token by
        ``hash_session_token``, never taken from the request. So a caller who
        somehow sent the stored hash gets a delete that matches nothing, not a
        delete of somebody else's session - the same property that makes a stolen
        session table useless for authenticating.

        Returns nothing. There is no partial outcome to report: the row is gone
        afterwards, whether or not it was there before.
        """
        uow = self._unit_of_work_factory.start()
        try:
            uow.sessions.delete_by_token_hash(hash_session_token(token))
            uow.commit()
        finally:
            uow.rollback()
