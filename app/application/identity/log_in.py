"""Proving an identity: the one operation that hands out a session."""

from dataclasses import dataclass
from datetime import datetime

from app.application.unit_of_work import UnitOfWorkFactory
from app.domain.identity.exception import InvalidCredentialsError
from app.domain.identity.password import PlainPassword
from app.domain.identity.password_hasher import PasswordHasher
from app.domain.identity.session import Session
from app.domain.identity.user import User


@dataclass(frozen=True)
class LoggedIn:
    """What a successful login hands back: who, until when, and the token.

    A named value rather than a three-tuple, and the reason is that its three
    fields are three different *kinds* of thing sitting in a fixed order - an
    identity, an expiry, and the one secret in the system that is not a hash.
    ``user, session, token = log_in.execute(...)`` is one transposition away from
    unpacking to "wrong but the right types" in a way nothing would catch, and a
    reader of the call site has to hold the order in their head to know which is
    which. Here the names are the order.

    It is also the boundary between the two halves of what a login produces:
    ``session`` and ``token`` are the same fact in its storable and its
    presentable form, and ``user`` is the identity that fact is about. Only the
    caller knows which of the two forms it wants - the API sends the token and
    drops the session, the CLI writes the token to a file and drops both - and
    this value lets each say so by name.

    Frozen, so a caller cannot reach in and swap the session for another one
    while keeping the token that matches it.
    """

    user: User
    session: Session
    token: str


class LogIn:
    """Exchange an address and a password for a session and its token.

    **This is the last moment the password exists in plaintext anywhere in the
    system.** It arrives as a string, is wrapped so the policy can judge it, is
    handed to the hasher to compare, and is never written, logged or returned.
    What leaves is a token, which is not a password and cannot be turned into
    one.

    **It answers one refusal for two situations.** An address with no account and
    an address whose password is wrong both raise ``InvalidCredentialsError``
    with the same words - decision 55, a foreign wallet reporting as a missing
    one, applied to the thing that hands out identities. Distinguishing them
    would let anyone with a list of addresses learn which ones are registered
    without ever guessing a password, and there is nothing a legitimate client
    would do differently with the two answers, since both mean "check what you
    typed".

    **That protection is on the words, not on the clock, and the gap is real.**
    An unknown address returns without hashing anything, so it answers in
    microseconds where a wrong password takes tens of milliseconds - and the
    difference is measurable from outside. The fix is to verify against a dummy
    hash when no credential is found, four lines and a constant, and it belongs
    in 2c with rate limiting: that addresses the same threat directly, and it
    also covers the sign-up path, which this cannot. Stated here rather than
    discovered in an incident.
    """

    def __init__(
        self, unit_of_work_factory: UnitOfWorkFactory, password_hasher: PasswordHasher
    ):
        self._unit_of_work_factory = unit_of_work_factory
        self._password_hasher = password_hasher

    def execute(self, email: str, password: str, now: datetime) -> LoggedIn:
        """Return the account these credentials belong to, and a fresh session.

        Raises ``InvalidCredentialsError`` if they do not match an account, and
        whatever ``PlainPassword`` raises for a password that could not be one -
        a value of the wrong type, or one outside the length policy. The second
        is worth being explicit about: a three-character password is refused as a
        *malformed* password rather than as a wrong one, and that leaks nothing
        because the length policy is public and every stored password satisfies
        it. Reporting "those details did not match" for a password that could
        never have matched would send somebody to reset a password they had typed
        correctly.

        **A new session every time, and the old ones are left alone.** Logging in
        twice from two terminals produces two rows, and this is deliberate rather
        than an oversight: the alternative is ending a session somebody is still
        using on another device, and "signing in logged me out somewhere else" is
        a worse surprise than a second row. Listing them and revoking one is a
        later feature; the store already supports it, since a session is a row
        and revocation is a deletion.

        ``now`` is passed in for the reason ``Session.issue`` takes it.
        """
        uow = self._unit_of_work_factory.start()
        try:
            user = uow.users.find_by_email(email)
            if user is None:
                raise InvalidCredentialsError("those details did not match an account")

            credential = uow.password_credentials.find_by_user_id(user.user_id)
            if credential is None:
                # An account with no password. Nothing creates one today, and
                # ``PasswordCredentialRepository`` explains why the shape exists:
                # Phase 2c's Google sign-in makes accounts that genuinely have no
                # password. Until then this branch is unreachable, and it is here
                # rather than absent because the alternative when it *does* become
                # reachable is not a refusal - it is ``None`` reaching the hasher
                # and raising a ``TypeError`` that reports as a 500.
                raise InvalidCredentialsError("those details did not match an account")

            if not self._password_hasher.verify(
                PlainPassword(password), credential.password_hash
            ):
                raise InvalidCredentialsError("those details did not match an account")

            session, token = Session.issue(user.user_id, now)
            uow.sessions.save(session)
            uow.commit()
            return LoggedIn(user=user, session=session, token=token)
        finally:
            uow.rollback()
